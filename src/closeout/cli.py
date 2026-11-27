"""Command-line entry points used by the Makefile: migrate, seed, reset-data."""

from __future__ import annotations

import argparse
import logging
import os

from closeout import clockdb, db

log = logging.getLogger("closeout.cli")


def seed_if_empty(scale: float) -> bool:
    with db.tx() as c:
        if db.scalar(c, "SELECT count(*) FROM accounts"):
            return False
    reset_data(scale)
    return True


def reset_data(scale: float) -> None:
    from closeout import seed

    with db.tx() as c:
        seed.wipe(c)
        clockdb.reset(c, scale=scale)
        seed.load(c)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    p = argparse.ArgumentParser(prog="closeout")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate")
    for name in ("seed", "seed-if-empty"):
        sp = sub.add_parser(name)
        sp.add_argument("--scale", type=float, default=float(os.environ.get("CLOSEOUT_TIME_SCALE", "1")))
    a = p.parse_args()
    if a.cmd == "migrate":
        db.migrate()
        print("migrations applied")
    elif a.cmd == "seed":
        db.migrate()
        reset_data(a.scale)
        print(f"seed loaded (clock scale {a.scale:g})")
    elif a.cmd == "seed-if-empty":
        db.migrate()
        print("seed loaded" if seed_if_empty(a.scale) else "seed already present")


if __name__ == "__main__":
    main()
