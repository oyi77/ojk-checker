"""HTTP scraper for iDebKu OJK website with session management and AJAX priming."""

from __future__ import annotations

import re
from typing import Any, Tuple
from urllib.parse import quote, urljoin

import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from slik_checker.config import settings
from slik_checker.logging_config import get_logger

logger = get_logger(__name__)

BASE64_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
)

AJAX_ENDPOINTS = {
    "jenis_debitur": "/Public/PendaftaranOnline/GetJDebitur",
    "kewarganegaraan": "/Public/PendaftaranOnline/GetKewarganegaraan",
    "identitas": "/Public/PendaftaranOnline/GetIdentitas",
}


def _base64encode(s: str) -> str:
    out, i, n = "", 0, len(s)
    while i < n:
        c1 = ord(s[i])
        i += 1
        if i == n:
            out += BASE64_CHARS[c1 >> 2] + BASE64_CHARS[(c1 & 3) << 4] + "=="
            break
        c2 = ord(s[i])
        i += 1
        if i == n:
            out += (
                BASE64_CHARS[c1 >> 2]
                + BASE64_CHARS[((c1 & 3) << 4) | ((c2 & 0xF0) >> 4)]
                + BASE64_CHARS[(c2 & 0xF) << 2]
                + "="
            )
            break
        c3 = ord(s[i])
        i += 1
        out += (
            BASE64_CHARS[c1 >> 2]
            + BASE64_CHARS[((c1 & 3) << 4) | ((c2 & 0xF0) >> 4)]
            + BASE64_CHARS[((c2 & 0xF) << 2) | ((c3 & 0xC0) >> 6)]
            + BASE64_CHARS[c3 & 0x3F]
        )
    return out


class Scraper:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9,id;q=0.8",
                "Cache-Control": "no-cache",
            }
        )
        self._primed = False

    def prime_session(self, jd_id: int = 1, kw_id: int = 1) -> None:
        """Simulate browser AJAX calls that prime the ASP.NET session."""
        base = str(settings.ideb_base_url)
        self.session.get(urljoin(base, AJAX_ENDPOINTS["jenis_debitur"]))
        self.session.get(
            urljoin(base, AJAX_ENDPOINTS["kewarganegaraan"]), params={"JDebitur": jd_id}
        )
        self.session.get(
            urljoin(base, AJAX_ENDPOINTS["identitas"]), params={"JDebitur": jd_id, "Warga": kw_id}
        )
        self._primed = True
        logger.debug("scraper_session_primed")

    @retry(
        stop=stop_after_attempt(settings.max_retries),
        wait=wait_exponential(multiplier=settings.retry_backoff),
    )
    def fetch_page(self, url: str) -> Tuple[str, BeautifulSoup]:
        resp = self.session.get(url, timeout=settings.request_timeout)
        resp.raise_for_status()
        return resp.text, BeautifulSoup(resp.text, "html.parser")

    @retry(
        stop=stop_after_attempt(settings.max_retries),
        wait=wait_exponential(multiplier=settings.retry_backoff),
    )
    def fetch_captcha(self) -> bytes:
        resp = self.session.get(str(settings.captcha_url), timeout=settings.request_timeout)
        resp.raise_for_status()
        ct = resp.headers.get("content-type", "")
        if not ct.startswith("image"):
            raise RuntimeError(f"Captcha not image: content-type={ct}")
        return resp.content

    @retry(
        stop=stop_after_attempt(settings.max_retries),
        wait=wait_exponential(multiplier=settings.retry_backoff),
    )
    def post_form(self, url: str, data: dict) -> Tuple[int, BeautifulSoup]:
        self.session.headers.update(
            {
                "Origin": str(settings.ideb_base_url),
                "Referer": str(settings.pre_register_url),
                "Content-Type": "application/x-www-form-urlencoded",
            }
        )
        resp = self.session.post(
            url, data=data, allow_redirects=True, timeout=settings.request_timeout
        )
        return resp.status_code, BeautifulSoup(resp.text, "html.parser")

    @staticmethod
    def extract_hidden_inputs(
        soup: BeautifulSoup, form_id: str = "FormPreRegister"
    ) -> dict[str, str]:
        form = soup.find("form", id=form_id)
        if not form:
            hidden = {}
            for m in re.finditer(r'<input[^>]*type="hidden"[^>]*>', str(soup)):
                name_m = re.search(r'name="([^"]*)"', m.group())
                val_m = re.search(r'value="([^"]*)"', m.group())
                if name_m:
                    hidden[name_m.group(1)] = val_m.group(1) if val_m else ""
            return hidden
        return {
            inp.get("name", ""): inp.get("value", "")
            for inp in form.find_all("input", type="hidden")
            if inp.get("name")
        }

    @staticmethod
    def extract_server_timestamp(html: str) -> Tuple[int, int, int, int, int, int]:
        match = re.search(r"new Date\('(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})'\)", html)
        if match:
            return tuple(int(g) for g in match.groups())  # type: ignore[return-value]
        raise RuntimeError("Server timestamp not found in HTML")

    @staticmethod
    def build_postm(html: str) -> str:
        y, mo, d, h, mi, s = Scraper.extract_server_timestamp(html)
        return _base64encode(f"{y:04d}-{mo:02d}-{d:02d}-{h:02d}-{mi:02d}-{s:02d}")

    def detect_kuota(self, html: str) -> bool:
        return bool(re.search(r"melebihi\s+kuota", html, re.IGNORECASE | re.DOTALL))

    def reset(self) -> None:
        self.session.cookies.clear()
        self._primed = False


scraper = Scraper()
