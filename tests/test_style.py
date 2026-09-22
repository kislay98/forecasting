"""House style: no em dash in any tracked-looking file (DECISIONS.md M1-17)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_PARTS = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache"}
SKIP_NAMES = {"uv.lock"}
EM_DASH = chr(0x2014)


def test_no_em_dash_anywhere():
    offenders = []
    for p in ROOT.rglob("*"):
        if not p.is_file() or SKIP_PARTS & set(p.relative_to(ROOT).parts) or p.name in SKIP_NAMES:
            continue
        try:
            text = p.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if EM_DASH in line:
                offenders.append(f"{p.relative_to(ROOT)}:{i}")
    assert not offenders, f"em dash found: {offenders}"
