"""Structural guard: no source file may exceed the 1000-line cap.

Enforces the project rule "never surpass 1000 lines per file" so a future
edit that balloons a module fails CI instead of silently drifting.
"""

from pathlib import Path

MAX_LINES = 1000

ROOT = Path(__file__).resolve().parent.parent
TARGETS = [ROOT / "slik_checker", ROOT / "tests"]


def _python_files():
    for base in TARGETS:
        if base.exists():
            yield from base.rglob("*.py")


def test_no_file_exceeds_line_cap():
    offenders = []
    for path in _python_files():
        with open(path, encoding="utf-8") as fh:
            n = sum(1 for _ in fh)
        if n > MAX_LINES:
            offenders.append((path.relative_to(ROOT), n))
    assert not offenders, (
        f"Files exceeding the {MAX_LINES}-line cap:\n"
        + "\n".join(f"  {p} ({n} lines)" for p, n in offenders)
    )
