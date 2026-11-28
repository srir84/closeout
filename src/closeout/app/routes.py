"""Every slice registers its pages here."""

from __future__ import annotations

from fastapi import FastAPI


def register(app: FastAPI) -> None:
    from closeout.app import (
        contractor,
        data_pages,
        homeowner,
        ops_pages,
        outreach_pages,
        runs_pages,
        town_pages,
    )

    data_pages.register(app)
    contractor.register(app)
    outreach_pages.register(app)
    runs_pages.register(app)
    homeowner.register(app)
    town_pages.register(app)
    ops_pages.register(app)
