"""The eval sets and their cases (evals 4.0). Every case is listed; a missing answer is a failure."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent / "sets"
SEALED = Path(__file__).parents[2] / "closeout-private" / "sealed-invoices"
MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".pdf": "application/pdf"}


@dataclass(frozen=True)
class Case:
    set: str
    id: str
    path: Path
    expected: dict[str, Any]
    meta: dict[str, Any]

    @property
    def mime(self) -> str:
        return MIME[self.path.suffix.lower()]


DEMO = Path(__file__).parents[1] / "data" / "demo"


def set_dir(name: str) -> Path:
    if name == "demo":
        return DEMO
    return SEALED if name == "sealed" else ROOT / name


def cases(name: str) -> list[Case]:
    d = set_dir(name)
    if name == "attacks":
        out = []
        for c in json.loads((d / "cases.json").read_text()):
            meta = {k: v for k, v in c.items() if k not in ("expected",)}
            out.append(
                Case(
                    name, c["id"] + "_attack", d / c["attack_file"], c["expected"], meta | {"twin": "attack"}
                )
            )
            out.append(
                Case(name, c["id"] + "_clean", d / c["clean_file"], c["expected"], meta | {"twin": "clean"})
            )
        return out
    truth = json.loads((d / "truth.json").read_text())
    return [
        Case(name, c["id"], d / c["file"], c["expected"], {k: v for k, v in c.items() if k != "expected"})
        for c in truth
    ]


def purpose(name: str) -> str:
    return "town_reply" if name == "town_replies" else "invoice"
