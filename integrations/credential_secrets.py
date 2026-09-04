"""Encrypted merchant payment credentials for the single-process pilot."""

from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path
from threading import RLock
from typing import Protocol

from cryptography.fernet import Fernet, InvalidToken


class CredentialSecretStore(Protocol):
    def put(self, connector_id: str, credentials: dict[str, str]) -> None: ...

    def get(self, connector_id: str) -> dict[str, str]: ...

    def delete(self, connector_id: str) -> None: ...


class CredentialStoreError(RuntimeError):
    """A safe, non-secret-bearing credential-store failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class FernetFileCredentialSecretStore:
    """Authenticated encrypted file storage for one application process."""

    def __init__(self, *, path: str | Path, encryption_key: str) -> None:
        if not str(path).strip():
            raise CredentialStoreError("credential_store_path_missing")
        try:
            self._fernet = Fernet(encryption_key.strip().encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise CredentialStoreError("credential_encryption_key_invalid") from exc
        self._path = Path(path)
        self._lock = RLock()

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
    ) -> "FernetFileCredentialSecretStore":
        values = os.environ if env is None else env
        key = values.get("CHARGEGUARD_CREDENTIAL_ENCRYPTION_KEY", "").strip()
        path = values.get("CHARGEGUARD_CREDENTIAL_STORE_PATH", "").strip()
        if not key:
            raise CredentialStoreError("credential_encryption_key_missing")
        if not path:
            raise CredentialStoreError("credential_store_path_missing")
        return cls(path=path, encryption_key=key)

    def put(self, connector_id: str, credentials: dict[str, str]) -> None:
        _validate_connector_id(connector_id)
        if not credentials or not all(
            isinstance(key, str)
            and key
            and isinstance(value, str)
            and bool(value)
            for key, value in credentials.items()
        ):
            raise CredentialStoreError("credential_payload_invalid")
        plaintext = json.dumps(
            credentials,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        token = self._fernet.encrypt(plaintext).decode("ascii")
        with self._lock:
            payload = self._read()
            payload[connector_id] = token
            self._write(payload)

    def get(self, connector_id: str) -> dict[str, str]:
        _validate_connector_id(connector_id)
        with self._lock:
            token = self._read().get(connector_id)
        if token is None:
            raise CredentialStoreError("credential_not_found")
        try:
            decoded = self._fernet.decrypt(token.encode("ascii"))
            credentials = json.loads(decoded.decode("utf-8"))
        except (InvalidToken, UnicodeError, ValueError, TypeError) as exc:
            raise CredentialStoreError("credential_decryption_failed") from exc
        if not isinstance(credentials, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in credentials.items()
        ):
            raise CredentialStoreError("credential_payload_invalid")
        return credentials

    def delete(self, connector_id: str) -> None:
        _validate_connector_id(connector_id)
        with self._lock:
            payload = self._read()
            if connector_id not in payload:
                return
            del payload[connector_id]
            self._write(payload)

    def _read(self) -> dict[str, str]:
        if not self._path.is_file():
            return {}
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CredentialStoreError("credential_store_unreadable") from exc
        if not isinstance(payload, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in payload.items()
        ):
            raise CredentialStoreError("credential_store_unreadable")
        return payload

    def _write(self, payload: dict[str, str]) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._path.with_suffix(f"{self._path.suffix}.tmp")
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, separators=(",", ":"), sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(self._path)
        except OSError as exc:
            raise CredentialStoreError("credential_store_write_failed") from exc


_cached_store: tuple[str, str, FernetFileCredentialSecretStore] | None = None
_cache_lock = RLock()


def credential_secret_store_from_env() -> FernetFileCredentialSecretStore:
    """Return one process-local store instance for the current configuration."""
    key = os.getenv("CHARGEGUARD_CREDENTIAL_ENCRYPTION_KEY", "").strip()
    path = os.getenv("CHARGEGUARD_CREDENTIAL_STORE_PATH", "").strip()
    if not key:
        raise CredentialStoreError("credential_encryption_key_missing")
    if not path:
        raise CredentialStoreError("credential_store_path_missing")
    global _cached_store
    with _cache_lock:
        if _cached_store is None or _cached_store[:2] != (path, key):
            _cached_store = (path, key, FernetFileCredentialSecretStore(path=path, encryption_key=key))
        return _cached_store[2]


def reset_credential_secret_store_cache() -> None:
    """Discard the process-local instance, primarily for configuration tests."""
    global _cached_store
    with _cache_lock:
        _cached_store = None


def _validate_connector_id(connector_id: str) -> None:
    if not isinstance(connector_id, str) or not connector_id or len(connector_id) > 200:
        raise CredentialStoreError("connector_id_invalid")
