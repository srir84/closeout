"""Temporal client set-up. The same pydantic data converter everywhere (design 3.8)."""

from __future__ import annotations

from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter

from closeout.settings import get_settings

DATA_CONVERTER = pydantic_data_converter


async def connect(address: str | None = None, namespace: str | None = None) -> Client:
    s = get_settings()
    return await Client.connect(
        address or s.temporal_address,
        namespace=namespace or s.temporal_namespace,
        data_converter=DATA_CONVERTER,
    )
