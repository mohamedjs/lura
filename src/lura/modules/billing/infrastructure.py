"""Lemon Squeezy's licence API, and the licence on disk."""

from __future__ import annotations

import json
import os
import platform
from pathlib import Path
from typing import Any

from ...shared import paths
from ...shared.http import HttpError, request_json
from .domain import License

API = "https://api.lemonsqueezy.com/v1/licenses"


def store_config() -> dict[str, Any]:
    data = json.loads(Path(__file__).with_name("store.json").read_text(encoding="utf-8"))
    for field, var in (("store_id", "LURA_LS_STORE_ID"), ("product_id", "LURA_LS_PRODUCT_ID"),
                       ("checkout_url", "LURA_CHECKOUT_URL")):
        if os.environ.get(var):
            data[field] = int(os.environ[var]) if field.endswith("_id") else os.environ[var]
    return data


class LicenseError(RuntimeError):
    pass


class LemonSqueezyClient:
    def __init__(self) -> None:
        self._config = store_config()

    def _call(self, action: str, form: dict[str, str]) -> dict[str, Any]:
        try:
            return request_json(f"{API}/{action}", {"Accept": "application/json"}, form=form, timeout=20)
        except HttpError as exc:
            # 4xx carries a JSON {"error": "..."} worth showing as-is.
            text = str(exc)
            if "{" in text:
                try:
                    return json.loads(text[text.index("{"):])
                except json.JSONDecodeError:
                    pass
            raise

    def _check_product(self, data: dict[str, Any]) -> None:
        """A key from someone else's Lemon Squeezy store must not unlock Pro."""
        meta = data.get("meta") or {}
        for field in ("store_id", "product_id"):
            want = self._config.get(field)
            if want is not None and meta.get(field) != want:
                raise LicenseError("That licence key is not for Lura Pro.")

    def activate(self, key: str) -> License:
        data = self._call("activate", {"license_key": key, "instance_name": platform.node() or "lura"})
        if not data.get("activated"):
            raise LicenseError(data.get("error") or "That licence key could not be activated.")
        self._check_product(data)
        lk = data.get("license_key") or {}
        meta = data.get("meta") or {}
        return License(key=key, instance_id=(data.get("instance") or {}).get("id", ""),
                       status=lk.get("status", ""), expires_at=lk.get("expires_at"),
                       customer_email=meta.get("customer_email", ""))

    def validate(self, lic: License) -> License:
        data = self._call("validate", {"license_key": lic.key, "instance_id": lic.instance_id})
        lk = data.get("license_key") or {}
        if data.get("valid"):
            self._check_product(data)
        lic.status = lk.get("status", "inactive") if data.get("valid") else (lk.get("status") or "inactive")
        lic.expires_at = lk.get("expires_at", lic.expires_at)
        return lic

    def deactivate(self, lic: License) -> None:
        self._call("deactivate", {"license_key": lic.key, "instance_id": lic.instance_id})

    @property
    def checkout_url(self) -> str:
        return self._config.get("checkout_url", "")

    @property
    def manage_url(self) -> str:
        return self._config.get("manage_url", "")


class LicenseRepository:
    def __init__(self, path: Path | None = None):
        self.path = path or paths.config_dir() / "license.json"

    def load(self) -> License:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return License(**{k: v for k, v in raw.items() if k in License().to_dict()})
        except (OSError, json.JSONDecodeError, TypeError):
            return License()

    def save(self, lic: License) -> None:
        paths.write_private(self.path, json.dumps(lic.to_dict(), indent=2) + "\n")

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
