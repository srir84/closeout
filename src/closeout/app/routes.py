"""Every slice registers its pages here."""

from __future__ import annotations

from fastapi import FastAPI


def register(app: FastAPI) -> None:
    from closeout.app import contractor, data_pages

    data_pages.register(app)
    contractor.register(app)
