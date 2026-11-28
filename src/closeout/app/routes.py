"""Every slice registers its pages here."""

from __future__ import annotations

from fastapi import FastAPI


def register(app: FastAPI) -> None:
    from closeout.app import contractor, data_pages, outreach_pages, runs_pages

    data_pages.register(app)
    contractor.register(app)
    outreach_pages.register(app)
    runs_pages.register(app)
