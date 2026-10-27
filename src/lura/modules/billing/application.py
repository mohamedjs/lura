"""The plan the user is on, and moving between plans."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from ...shared import events
from ...shared.events import EventBus
from ...shared.http import HttpError
from .domain import FREE, PLANS, PRO, License, due_for_check, entitled, masked_email
from .infrastructure import LemonSqueezyClient, LicenseError, LicenseRepository

log = logging.getLogger(__name__)


class BillingService:
    def __init__(self, repo: LicenseRepository | None = None, client: LemonSqueezyClient | None = None,
                 bus: EventBus | None = None):
        self._repo = repo or LicenseRepository()
        self._client = client or LemonSqueezyClient()
        self._bus = bus
        self._license = self._repo.load()
        self._lock = threading.Lock()

    def is_pro(self) -> bool:
        return entitled(self._license)

    def plan(self) -> dict[str, Any]:
        lic = self._license
        current = PRO if self.is_pro() else FREE
        return {"plan": current, "plans": PLANS, "status": lic.status or None,
                "expires_at": lic.expires_at, "email": masked_email(lic.customer_email),
                "has_license": lic.present, "checkout_url": self._client.checkout_url,
                "manage_url": self._client.manage_url}

    def _publish(self) -> None:
        if self._bus:
            self._bus.publish(events.PLAN_CHANGED, plan=self.plan()["plan"])

    def activate(self, key: str) -> dict[str, Any]:
        key = key.strip()
        if not key:
            raise LicenseError("Paste your licence key.")
        lic = self._client.activate(key)
        lic.last_validated = lic.last_checked = time.time()
        with self._lock:
            self._license = lic
            self._repo.save(lic)
        self._publish()
        return self.plan()

    def deactivate(self) -> dict[str, Any]:
        lic = self._license
        if lic.present:
            try:
                self._client.deactivate(lic)   # frees the activation slot for another machine
            except Exception as exc:
                log.warning("Could not deactivate the licence remotely: %s", exc)
        with self._lock:
            self._license = License()
            self._repo.clear()
        self._publish()
        return self.plan()

    def revalidate(self, force: bool = False) -> None:
        lic = self._license
        if not lic.present or not (force or due_for_check(lic)):
            return
        was = self.is_pro()
        lic.last_checked = time.time()
        try:
            self._client.validate(lic)
            if lic.status == "active":
                lic.last_validated = time.time()
        except (HttpError, LicenseError) as exc:
            # Offline or the server is down: keep the grace period running.
            log.info("Licence check failed (%s); grace period applies.", exc)
        with self._lock:
            self._repo.save(lic)
        if self.is_pro() != was:
            self._publish()
