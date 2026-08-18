"""HTTP scraper for iDebKu OJK website with session management and AJAX priming."""

from __future__ import annotations

import re
import time
from urllib.parse import urljoin

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
        """Best-effort warm-up of the ASP.NET session via the portal's AJAX
        cascades. Failures are non-fatal: the subsequent form-page load still
        establishes the session, so a slow/blocked AJAX must not abort a run.
        """
        base = str(settings.ideb_base_url)
        calls = [
            urljoin(base, AJAX_ENDPOINTS["jenis_debitur"]),
            urljoin(base, AJAX_ENDPOINTS["kewarganegaraan"]) + f"?JDebitur={jd_id}",
            urljoin(base, AJAX_ENDPOINTS["identitas"]) + f"?JDebitur={jd_id}&Warga={kw_id}",
        ]
        for url in calls:
            try:
                self.session.get(url, timeout=settings.request_timeout)
            except Exception as e:  # noqa: BLE001 - best-effort priming
                logger.debug(f"prime_session_call_failed: {url} | {e}")
        self._primed = True
        logger.debug("scraper_session_primed")

    @retry(
        stop=stop_after_attempt(settings.max_retries),
        wait=wait_exponential(multiplier=settings.retry_backoff),
    )
    def fetch_page(self, url: str) -> tuple[str, BeautifulSoup]:
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
    def post_form(self, url: str, data: dict) -> tuple[int, BeautifulSoup]:
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
        # Status page has no form id; fall back to the first <form> so the
        # anti-forgery token is still captured.
        form = soup.find("form", id=form_id) if form_id else None
        if not form:
            form = soup.find("form")
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
    def extract_server_timestamp(html: str) -> tuple[int, int, int, int, int, int]:
        match = re.search(r"new Date\('(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})'\)", html)
        if match:
            return tuple(int(g) for g in match.groups())  # type: ignore[return-value]
        # Step-2+ pages may omit the literal; fall back to current local time so
        # postm never crashes the wizard (server accepts recent timestamps).
        now = time.localtime()
        return (now.tm_year, now.tm_mon, now.tm_mday, now.tm_hour, now.tm_min, now.tm_sec)

    @staticmethod
    def build_postm(
        html: str, server_ts: tuple[int, int, int, int, int, int] | None = None
    ) -> str:
        if server_ts is None:
            server_ts = Scraper.extract_server_timestamp(html)
        y, mo, d, h, mi, s = server_ts
        # Mirror the portal's cmdEncrypt() exactly: YYYY-M-D-HH-MM-SS
        # (month/day UNpadded, h/m/s zero-padded) — the server decodes and
        # validates this anti-replay timestamp, so the format must match.
        return _base64encode(f"{y}-{mo}-{d}-{h:02d}-{mi:02d}-{s:02d}")

    def detect_kuota(self, html: str) -> bool:
        return bool(re.search(r"melebihi\s+kuota", html, re.IGNORECASE | re.DOTALL))

    def reset(self) -> None:
        self.session.cookies.clear()
        self._primed = False

    def save_session(self, path: str) -> None:
        """Persist session cookies so a captcha fetched in one process can be
        submitted in another. The portal stores the expected captcha value
        server-side per session, so the submit must reuse the same session.
        """
        import json

        with open(path, "w", encoding="utf-8") as f:
            json.dump(requests.utils.dict_from_cookiejar(self.session.cookies), f)

    def load_session(self, path: str) -> None:
        """Restore persisted session cookies (see save_session)."""
        import json

        with open(path, encoding="utf-8") as f:
            self.session.cookies = requests.utils.cookiejar_from_dict(json.load(f))
        self._primed = True


scraper = Scraper()
