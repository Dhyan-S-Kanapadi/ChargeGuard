from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from threading import RLock
from typing import Any

from core.state import ChargebackState, MerchantProfile


class InMemoryStore:
    """Thread-safe repository with optional JSON persistence for local durability."""

    def __init__(self, path: str | Path | None = None) -> None:
        self._lock = RLock()
        self._path = Path(path) if path else None
        self._merchants: dict[str, MerchantProfile] = {}
        self._disputes: dict[str, dict[str, Any]] = {}
        self._load()

    @classmethod
    def from_env(cls) -> "InMemoryStore":
        return cls(path=os.getenv("CHARGEGUARD_STORE_PATH"))

    def clear(self) -> None:
        with self._lock:
            self._merchants.clear()
            self._disputes.clear()
            self._save()

    def create_merchant(self, profile: MerchantProfile) -> bool:
        merchant_id = profile["merchant_id"]
        with self._lock:
            if merchant_id in self._merchants:
                return False
            self._merchants[merchant_id] = deepcopy(profile)
            self._save()
            return True

    def get_merchant(self, merchant_id: str) -> MerchantProfile | None:
        with self._lock:
            profile = self._merchants.get(merchant_id)
            return deepcopy(profile) if profile else None

    def create_dispute(self, state: ChargebackState) -> bool:
        chargeback_id = state["chargeback_id"]
        now = datetime.now(timezone.utc)
        with self._lock:
            if chargeback_id in self._disputes:
                return False
            self._disputes[chargeback_id] = {
                "chargeback_id": chargeback_id,
                "status": "received",
                "state": deepcopy(state),
                "error": None,
                "created_at": now,
                "updated_at": now,
            }
            self._save()
            return True

    def update_dispute(
        self,
        chargeback_id: str,
        *,
        status: str,
        state: ChargebackState | None = None,
        error: str | None = None,
    ) -> None:
        with self._lock:
            record = self._disputes[chargeback_id]
            record["status"] = status
            if state is not None:
                record["state"] = deepcopy(state)
            record["error"] = error
            record["updated_at"] = datetime.now(timezone.utc)
            self._save()

    def get_dispute(self, chargeback_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._disputes.get(chargeback_id)
            return deepcopy(record) if record else None

    def list_disputes(self) -> list[dict[str, Any]]:
        with self._lock:
            records = [deepcopy(record) for record in self._disputes.values()]
        return sorted(records, key=lambda record: record["created_at"], reverse=True)

    def _load(self) -> None:
        if self._path is None or not self._path.is_file():
            return

        with self._path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle, object_hook=_decode_json_value)

        merchants = payload.get("merchants", {})
        disputes = payload.get("disputes", {})
        if not isinstance(merchants, dict) or not isinstance(disputes, dict):
            raise ValueError("Store file must contain object maps for merchants and disputes.")

        self._merchants = deepcopy(merchants)
        self._disputes = deepcopy(disputes)

    def _save(self) -> None:
        if self._path is None:
            return

        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "merchants": self._merchants,
            "disputes": self._disputes,
        }
        temp_path = self._path.with_suffix(f"{self._path.suffix}.tmp")
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(_encode_json_value(payload), handle, indent=2, sort_keys=True)
            handle.write("\n")
        temp_path.replace(self._path)


def _encode_json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return {
            "__chargeguard_type__": "datetime",
            "value": value.isoformat(),
        }
    if isinstance(value, dict):
        return {key: _encode_json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_encode_json_value(item) for item in value]
    return value


def _decode_json_value(value: dict[str, Any]) -> Any:
    if value.get("__chargeguard_type__") == "datetime":
        return datetime.fromisoformat(str(value["value"]))
    return value


store = InMemoryStore.from_env()
