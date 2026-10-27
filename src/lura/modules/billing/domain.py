"""Plans and licences.

One paid plan for now: **Pro, $5 a month**. Free covers everything except
adding connectors beyond the three built-ins.

Payment and licence keys come from Lemon Squeezy: it is the merchant of
record (it collects VAT/sales tax worldwide), it issues a licence key per
subscription, and its licence API is public — a desktop app can activate
and validate a key with no server of ours in between. When the subscription
lapses the key's status turns ``expired`` and Pro switches off.

The check runs on the user's machine, so it can be patched out by someone
determined. That is true of every client-side licence; the deterrent is
that it is not worth $5 of effort.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass

FREE, PRO = "free", "pro"

PLANS = {
    FREE: {"price_usd": 0, "interval": None, "features": ["voice", "all_providers", "builtin_connectors"]},
    PRO: {"price_usd": 5, "interval": "month",
          "features": ["voice", "all_providers", "builtin_connectors", "custom_connectors"]},
}

#: Re-check the licence this often while online.
REVALIDATE_SECONDS = 24 * 3600
#: Keep Pro this long without reaching the licence server (a laptop on a
#: plane should not lose its connectors).
OFFLINE_GRACE_SECONDS = 7 * 24 * 3600

ACTIVE_STATUSES = ("active",)


@dataclass
class License:
    key: str = ""
    instance_id: str = ""
    status: str = ""             # Lemon Squeezy: active | inactive | expired | disabled
    expires_at: str | None = None
    customer_email: str = ""
    last_validated: float = 0.0  # last time the server said "valid"
    last_checked: float = 0.0    # last attempt, successful or not

    @property
    def present(self) -> bool:
        return bool(self.key and self.instance_id)

    def to_dict(self) -> dict:
        return asdict(self)


def entitled(lic: License, now: float | None = None) -> bool:
    """Is this licence Pro right now? Pure, so the rule is testable."""
    now = time.time() if now is None else now
    if not lic.present or lic.status not in ACTIVE_STATUSES:
        return False
    return now - lic.last_validated <= OFFLINE_GRACE_SECONDS


def due_for_check(lic: License, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    return lic.present and now - lic.last_checked >= REVALIDATE_SECONDS


def masked_email(email: str) -> str:
    name, _, domain = email.partition("@")
    return f"{name[:2]}…@{domain}" if domain else ""
