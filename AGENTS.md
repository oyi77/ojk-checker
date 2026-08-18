# AGENTS.md — ojk-checker (slik-checker)

## This repo
Automated periodic SLIK (Indonesian credit history / BI Checking) registration and
status-checking against OJK's public **iDebKu** portal (idebku.ojk.go.id). Submits
pre-registrations, solves the captcha, and polls registration status on a schedule,
notifying via Telegram/Email.

Stack: Python 3.10+. Core deps: requests, beautifulsoup4, selenium, apscheduler,
pytesseract, easyocr, ddddocr, Pillow, streamlit, pandas, pydantic-settings, structlog, tenacity.
Domain: government-portal web automation (captcha solving + form submission + scheduling).

## Rules — thin loader, no submodule
Rules are NOT vendored into this repo. Load them centrally from `~/.1ai`:

```bash
cat ~/.1ai/core/RULES.md      # primary engineering rules
cat ~/.1ai/core/GATE.md       # pre-ship gate
```

If `~/.1ai` or auto-load is missing, run `bash ~/.1ai/scripts/setup-dev.sh`.

Hard rules (always apply):
1. Read code before writing code.
2. No completion claim without literal receipt (test run / tool output).
3. Compile/test/use like a real user before claiming work is ready.
4. Task must match this repo domain (iDebKu SLIK automation).
5. Run GATE.md before commit/PR.

## Repo-specific conventions
- **Module layout** (one job each): `orchestrator` (engine), `scraper` (HTTP/session),
  `parser` (response → status), `captcha` (multi-engine OCR + external fallback),
  `models` (sqlite repo), `notifier` (telegram/email), `scheduler` (apscheduler daemon),
  `cli` (argparse entry), `config`/`exceptions`/`logging_config` (cross-cutting).
- **Error model**: raise `SlikError` subclasses (`CaptchaSolverError`, `ScrapingError`,
  `QuotaFullError`, ...). Retryable = `CaptchaSolverError`/`ScrapingError`; everything
  else is structural/fatal and must NOT be silently retried.
- **Form filling** is name-agnostic (substring match on control names) in
  `Orchestrator._build_registration_payload` so it survives minor iDebKu markup changes.
- **Never swallow `Exception`** to retry — that hides `NameError`/programming bugs. Catch
  the specific `SlikError` subclasses.
- Tests: pytest, `tests/`, autouse `patch_settings` fixture redirects `db_path` to tmp.
  Mock `scraper`/`captcha_solver`/`parser`/`notifier`/`db` — do NOT load OCR models or hit
  the network in unit tests. Coverage gate: 70% (see pyproject `[tool.coverage]`).
- Lint/format: `ruff` (E/F/I/N/W/UP/B/C4/SIM), `mypy --ignore-missing-imports`.
- CLI entry: `slik-checker` (`python -m slik_checker.cli`); UI: `streamlit run slik_checker/ui/app.py`.

## Commands
- Dev:   `python -m slik_checker.cli init` (then `register` / `check` / `schedule` / `run` / `ui`)
- Test:  `pytest`  (fast unit suite; orchestrator/captcha mocked)
- Lint:  `ruff check slik_checker`
- Type:  `mypy slik_checker --ignore-missing-imports`
- Build: `python -m compileall slik_checker`
