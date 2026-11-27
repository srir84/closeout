"""Before every push and in CI: the public repo must not contain private names (test strategy 5.4).

The list of names lives outside the repo:
  - $CLOSEOUT_PRIVATE_NAMES (newline-separated; CI sets it from a repository secret), or
  - ../closeout-private/private-names.txt next to the repo.
Also refuses absolute home-folder paths and email addresses outside made-up domains.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
OK_EMAIL_DOMAINS = (
    ".example",
    ".test",
    "example.com",
    "example.org",
    "example.net",
    "closeout.local",
    "users.noreply.github.com",
    "anthropic.com",
)
# Model answers from the model test (made-up invoices); a model invented a domain once.
EMAIL_EXEMPT_PATHS = ("spikes/model-test/results/",)
ALLOW_MARK = "publish-check: allow"
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
HOME_PATH = re.compile(r"/(Users|home)/[a-z][a-z0-9_-]+/")
SKIP_SUFFIX = (".png", ".jpg", ".jpeg", ".pdf", ".gif", ".mp4", ".webm", ".ico", ".woff", ".woff2", ".lock")


def private_names() -> list[str]:
    env = os.environ.get("CLOSEOUT_PRIVATE_NAMES", "")
    lines = env.splitlines()
    f = ROOT.parent / "closeout-private" / "private-names.txt"
    if not lines and f.exists():
        lines = f.read_text().splitlines()
    return [ln.strip().lower() for ln in lines if ln.strip() and not ln.startswith("#")]


def tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    return [ROOT / p for p in out.splitlines() if p and (ROOT / p).is_file()]


def history_hits(names: list[str]) -> list[str]:
    """Names anywhere in git history: every commit's added lines, file names and messages."""
    if not names:
        return []
    log = subprocess.run(
        ["git", "log", "--all", "-p", "--format=%H %an %ae%n%B", "--name-status", "--text"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        errors="ignore",
        check=True,
    ).stdout.lower()
    return [f"git history: private name ({len(n)} chars)" for n in names if n in log]


def scan(files: list[Path], names: list[str]) -> list[str]:
    hits = []
    for p in files:
        rel = p.relative_to(ROOT).as_posix()
        if rel == "tools/publish_check.py":
            continue
        try:
            raw = p.read_bytes()
        except OSError:
            continue
        low = raw.decode("latin-1").lower()
        for n in names:
            if n in low or n in rel.lower():
                hits.append(f"{rel}: private name ({len(n)} chars)")
        if rel.endswith(SKIP_SUFFIX):
            continue
        text = raw.decode("utf-8", errors="ignore")
        for m in HOME_PATH.finditer(text):
            hits.append(f"{rel}: home-folder path {m.group(0)}")
        for m in EMAIL.finditer(text):
            if rel.startswith(EMAIL_EXEMPT_PATHS):
                break
            line = text[text.rfind("\n", 0, m.start()) + 1 : text.find("\n", m.end())]
            if ALLOW_MARK in line:
                continue
            addr = m.group(0).lower()
            if not addr.endswith(OK_EMAIL_DOMAINS) and not addr.split("@")[1].endswith(OK_EMAIL_DOMAINS):
                hits.append(f"{rel}: email outside made-up domains: {addr.split('@')[1]}")
    return hits


def main() -> int:
    names = private_names()
    if not names and os.environ.get("CI"):
        print("publish check: CLOSEOUT_PRIVATE_NAMES is not set in CI")
        return 1
    hits = scan(tracked_files(), names)
    if "--history" in sys.argv:
        hits += history_hits(names)
    for h in hits:
        print("  " + h)
    print(f"publish check: {len(names)} private names, {len(hits)} hits")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
