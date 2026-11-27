"""Sender safety checks (R-OUT-7)."""

from __future__ import annotations

import httpx

from closeout.settings import Settings

LOCAL_SMTP_HOSTS = {"mailpit", "localhost", "127.0.0.1", "::1"}
ALLOWED_TLDS = (".example", ".test")


def allowed_recipient(address: str) -> bool:
    """Only made-up domains: the address must end in .example or .test."""
    a = address.strip().lower()
    if a.count("@") != 1:
        return False
    local, domain = a.split("@")
    if not local or not domain or domain.startswith(".") or ".." in domain:
        return False
    return any(domain.endswith(t) and len(domain) > len(t) for t in ALLOWED_TLDS)


def config_problems(s: Settings) -> list[str]:
    problems = []
    if s.smtp_host.strip().lower() not in LOCAL_SMTP_HOSTS:
        problems.append(f"SMTP_HOST must be mailpit or loopback, not {s.smtp_host!r}")
    if s.smtp_timeout_seconds >= s.sender_lease_seconds:
        problems.append("SMTP_TIMEOUT_SECONDS must be shorter than SENDER_LEASE_SECONDS")
    return problems


def mailpit_problems(s: Settings) -> list[str]:
    try:
        r = httpx.get(s.mailpit_api_url.rstrip("/") + "/api/v1/info", timeout=5)
        r.raise_for_status()
        if "Version" not in r.json():
            return ["MAILPIT_API_URL does not look like Mailpit"]
    except Exception as e:
        return [f"Mailpit API not reachable at {s.mailpit_api_url}: {type(e).__name__}"]
    return []


def startup_problems(s: Settings) -> list[str]:
    return config_problems(s) or mailpit_problems(s)
