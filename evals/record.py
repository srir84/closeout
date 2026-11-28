"""`make record`: read every document in the given sets live with both local models and save replay files.

Runs on the owner's Mac with Ollama. Replay files change only through a reviewed pull request.
  uv run python -m evals.record regression development attacks messy town_replies
"""

from __future__ import annotations

import sys
import time

from closeout.gateway.core import Gateway, models_lock
from closeout.settings import Settings
from evals.sets import cases, purpose


def main(names: list[str]) -> int:
    s = Settings(closeout_mode="full", ollama_url="http://127.0.0.1:11434")
    gw = Gateway(s)
    lock = models_lock(gw.replay_dir.parent / "models.lock")
    for r in ("A", "B"):
        live = gw.digest(r)  # type: ignore[arg-type]
        if live != lock[r].digest:
            print(
                f"reader {r}: installed digest {live[:12]} differs from evals/models.lock {lock[r].digest[:12]}"
            )
            return 2
    n_new = n_have = 0
    for name in names:
        for c in cases(name):
            data = c.path.read_bytes()
            looks = [("A", purpose(name)), ("B", purpose(name))]
            if purpose(name) == "invoice":
                looks.append(("B", "amount_marks"))
            for reader, what in looks:
                import hashlib

                from closeout.gateway.core import replay_key

                key = replay_key(
                    reader,
                    lock[reader].model,
                    lock[reader].digest,
                    what,  # type: ignore[arg-type]
                    hashlib.sha256(data).hexdigest(),
                )
                if (gw.replay_dir / reader / f"{key}.json").exists():
                    n_have += 1
                    continue
                t = time.monotonic()
                res = gw.read(data, c.mime, reader, what)  # type: ignore[arg-type]
                gw.save_replay(res)
                n_new += 1
                print(
                    f"{name} {c.id} {reader} {what} {time.monotonic() - t:.1f}s valid={res.valid}", flush=True
                )
    print(f"recorded {n_new} new answers ({n_have} already saved)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
