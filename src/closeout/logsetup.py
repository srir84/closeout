"""One logging setup for every service.

httpx logs each request's full URL at INFO. Mailpit searches put an email address in the URL (privacy request,
simulator), so request logging from httpx and httpcore is kept at WARNING. The privacy canary checks the logs.
"""

from __future__ import annotations

import logging

QUIET = ("httpx", "httpcore")


def setup(fmt: str = "%(asctime)s %(name)s %(levelname)s %(message)s") -> None:
    logging.basicConfig(level=logging.INFO, format=fmt)
    for name in QUIET:
        logging.getLogger(name).setLevel(logging.WARNING)
