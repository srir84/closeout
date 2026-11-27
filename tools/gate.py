"""The trusted `gate` check (test strategy 5.4).

Runs from a pull_request_target workflow, so it always uses main's copy of this file. It reads the
pull request's diff through the GitHub API and never runs the pull request's code. It lists:
  - changes to protected paths (from main's CODEOWNERS), which need the person's approval, and
  - suspicious changes (skips, xfails, pragmas, lower thresholds, deleted asserts, ...).
It posts the list on the pull request. It doesn't decide anything; GitHub's code-owner rule does.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parents[1]
MARK = "<!-- closeout-gate -->"
SUSPICIOUS_ADDED = [
    (r"pytest\.mark\.skip|pytest\.skip\(|skipif", "skip"),
    (r"xfail", "xfail"),
    (r"pragma:\s*no cover", "pragma: no cover"),
    (r"pragma:\s*no mutate", "pragma: no mutate"),
    (r"type:\s*ignore", "type: ignore"),
    (r"#\s*noqa", "noqa"),
    (r"\bassume\(", "hypothesis assume("),
    (r"max_examples\s*=\s*\d+", "hypothesis example count changed"),
    (r"(THRESHOLD|threshold|target)\s*=\s*\d", "threshold changed"),
]
SUSPICIOUS_REMOVED = [(r"^\s*assert\b", "assert deleted"), (r"\bexpect\(", "browser expectation deleted")]


def codeowner_patterns(text: str) -> list[str]:
    pats = []
    for line in text.splitlines():
        line = line.split("#")[0].strip()
        if line:
            pats.append(line.split()[0])
    return pats


def is_protected(path: str, patterns: list[str]) -> bool:
    for p in patterns:
        q = p.lstrip("/")
        if q.endswith("/"):
            if path.startswith(q):
                return True
        elif fnmatch.fnmatch(path, q) or fnmatch.fnmatch(path, q + "/*") or path == q:
            return True
    return False


def review(files: list[dict], patterns: list[str]) -> tuple[list[str], list[str]]:
    protected, suspicious = [], []
    for f in files:
        name = f["filename"]
        if is_protected(name, patterns) or is_protected(f.get("previous_filename", ""), patterns):
            protected.append(f"`{name}` ({f['status']})")
        if f["status"] == "removed" and name.startswith(("tests/", "evals/")):
            suspicious.append(f"`{name}`: test or golden file deleted")
        prev = f.get("previous_filename", "")
        if (f["status"] == "removed" and name.startswith("src/closeout/core/")) or (
            prev.startswith("src/closeout/core/") and not name.startswith("src/closeout/core/")
        ):
            suspicious.append(f"`{prev or name}`: code moved out of core/")
        for line in (f.get("patch") or "").splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                for rx, label in SUSPICIOUS_ADDED:
                    if re.search(rx, line[1:]):
                        suspicious.append(f"`{name}`: {label}: `{line[1:].strip()[:100]}`")
            elif line.startswith("-") and not line.startswith("---"):
                for rx, label in SUSPICIOUS_REMOVED:
                    if re.search(rx, line[1:]):
                        suspicious.append(f"`{name}`: {label}: `{line[1:].strip()[:100]}`")
    return protected, suspicious


def private_name_hits(files: list[dict], names: list[str]) -> list[str]:
    """Scan the diff as data for private names (fork pull requests run no other secret-bearing check)."""
    hits = []
    for f in files:
        blob = (f["filename"] + "\n" + (f.get("patch") or "")).lower()
        if any(n in blob for n in names):
            hits.append(f"`{f['filename']}`: contains a private name")
    return hits


def body(protected: list[str], suspicious: list[str]) -> str:
    out = [MARK, "## Gate check", ""]
    out.append(
        f"**Protected paths changed: {len(protected)}**"
        + (" — these need the code owner's approval." if protected else "")
    )
    out += [f"- {p}" for p in protected]
    out += [
        "",
        f"**Suspicious changes: {len(suspicious)}**"
        + (" — each needs the person's OK." if suspicious else ""),
    ]
    out += [f"- {s}" for s in suspicious[:200]]
    return "\n".join(out) + "\n"


def api(url: str, token: str, data: dict | None = None, method: str = "GET") -> object:
    req = urllib.request.Request(
        url,
        method=method,
        data=json.dumps(data).encode() if data else None,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        txt = r.read().decode()
        return json.loads(txt) if txt else None


def main() -> int:
    token = os.environ["GITHUB_TOKEN"]
    repo = os.environ["GITHUB_REPOSITORY"]
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    n = event["pull_request"]["number"]
    base = f"https://api.github.com/repos/{repo}"
    files: list[dict] = []
    page = 1
    while True:
        batch = api(f"{base}/pulls/{n}/files?per_page=100&page={page}", token)
        assert isinstance(batch, list)
        files += batch
        if len(batch) < 100:
            break
        page += 1
    patterns = codeowner_patterns((ROOT / ".github" / "CODEOWNERS").read_text())
    protected, suspicious = review(files, patterns)
    names = [
        n.strip().lower()
        for n in os.environ.get("CLOSEOUT_PRIVATE_NAMES", "").splitlines()
        if n.strip() and not n.startswith("#")
    ]
    name_hits = private_name_hits(files, names)
    text = body(protected, suspicious + name_hits)
    Path(os.environ.get("GITHUB_STEP_SUMMARY", "/dev/null")).write_text(text)
    comments = api(f"{base}/issues/{n}/comments?per_page=100", token)
    assert isinstance(comments, list)
    mine = [c for c in comments if MARK in (c.get("body") or "")]
    if mine:
        api(f"{base}/issues/comments/{mine[0]['id']}", token, {"body": text}, method="PATCH")
    else:
        api(f"{base}/issues/{n}/comments", token, {"body": text}, method="POST")
    print(text)
    return 1 if name_hits else 0


if __name__ == "__main__":
    sys.exit(main())
