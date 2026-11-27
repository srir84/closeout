"""HMACs over addresses: suppression list and approval recipient claims (design 3.6, 3.17)."""

from __future__ import annotations

import hashlib
import hmac

from closeout import keys


def normalise_email(address: str) -> str:
    return address.strip().lower()


def email_hmac(address: str) -> str:
    return hmac.new(keys.hmac_key(), normalise_email(address).encode(), hashlib.sha256).hexdigest()
