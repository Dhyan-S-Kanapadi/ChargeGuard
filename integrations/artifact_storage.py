"""Private artifact storage contracts.

Production deliberately has no provider implementation until an operator chooses
an object store and managed credential backend.  The local adapter is restricted
to development and test environments.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
from typing import Protocol

from core.runtime import RuntimeConfigurationError, runtime_environment


class ArtifactStorageError(RuntimeError):
    """Safe artifact storage failure; never include object contents."""


class ArtifactMissingError(ArtifactStorageError):
    pass


class ArtifactIntegrityError(ArtifactStorageError):
    pass


class ArtifactImmutableError(ArtifactStorageError):
    pass


@dataclass(frozen=True)
class StoredArtifact:
    object_key: str
    sha256: str
    size_bytes: int


class ArtifactStorage(Protocol):
    def put_immutable(self, object_key: str, content: bytes) -> StoredArtifact: ...

    def read_verified(self, object_key: str, expected_sha256: str) -> bytes: ...

    def discard_uncommitted(self, object_key: str, expected_sha256: str) -> None: ...


def artifact_object_key(*, merchant_id: str, chargeback_id: str, artifact_id: str, filename: str) -> str:
    """Create a non-user-controlled, tenant/case-scoped object key."""
    if not all(value and "/" not in value and "\\" not in value for value in (merchant_id, chargeback_id, artifact_id)):
        raise ValueError("Artifact identity is invalid.")
    safe_name = Path(filename).name
    if safe_name != filename or safe_name in {"", ".", ".."}:
        raise ValueError("Artifact filename is invalid.")
    return f"merchants/{merchant_id}/cases/{chargeback_id}/artifacts/{artifact_id}/{safe_name}"


class LocalArtifactStorage:
    """Write-once private files for development and tests only."""

    def __init__(self, root: str | Path) -> None:
        if runtime_environment() not in {"development", "test"}:
            raise RuntimeConfigurationError("Local artifact storage is development/test only.")
        self.root = Path(root).resolve()

    def _path(self, object_key: str) -> Path:
        parts = Path(object_key).parts
        if not object_key or Path(object_key).is_absolute() or ".." in parts:
            raise ArtifactStorageError("Invalid artifact object key.")
        path = (self.root / object_key).resolve()
        if self.root != path and self.root not in path.parents:
            raise ArtifactStorageError("Invalid artifact object key.")
        return path

    def put_immutable(self, object_key: str, content: bytes) -> StoredArtifact:
        path = self._path(object_key)
        digest = hashlib.sha256(content).hexdigest()
        if path.exists():
            existing = path.read_bytes()
            if hashlib.sha256(existing).hexdigest() != digest:
                raise ArtifactImmutableError("Artifact object already exists.")
            return StoredArtifact(object_key, digest, len(existing))
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = path.with_name(path.name + ".tmp")
        try:
            with temporary.open("xb") as handle:
                handle.write(content)
            temporary.replace(path)
            path.chmod(0o600)
        except FileExistsError as exc:
            raise ArtifactImmutableError("Artifact object already exists.") from exc
        persisted = path.read_bytes()
        if hashlib.sha256(persisted).hexdigest() != digest:
            raise ArtifactIntegrityError("Artifact write verification failed.")
        return StoredArtifact(object_key, digest, len(persisted))

    def read_verified(self, object_key: str, expected_sha256: str) -> bytes:
        path = self._path(object_key)
        try:
            content = path.read_bytes()
        except FileNotFoundError as exc:
            raise ArtifactMissingError("Artifact object is unavailable.") from exc
        if hashlib.sha256(content).hexdigest() != expected_sha256:
            raise ArtifactIntegrityError("Artifact integrity verification failed.")
        return content

    def discard_uncommitted(self, object_key: str, expected_sha256: str) -> None:
        """Remove a newly written object only when persistence never succeeded.

        This deliberately is not a retention/deletion API: callers may use it
        solely to compensate for a failed metadata transaction, and it refuses
        to unlink bytes whose checksum does not exactly match the new object.
        """
        path = self._path(object_key)
        try:
            content = path.read_bytes()
        except FileNotFoundError:
            return
        if hashlib.sha256(content).hexdigest() != expected_sha256:
            raise ArtifactIntegrityError("Uncommitted artifact cleanup verification failed.")
        path.unlink()


def artifact_storage() -> ArtifactStorage:
    environment = runtime_environment()
    if environment == "production":
        raise RuntimeConfigurationError(
            "Production artifact storage requires an approved object-storage provider and managed credential backend."
        )
    if environment not in {"development", "test"}:
        raise RuntimeConfigurationError("Artifact storage is unavailable outside development/test.")
    return LocalArtifactStorage(
        os.getenv("CHARGEGUARD_ARTIFACT_LOCAL_DIR")
        or os.getenv("REBUTTAL_OUTPUT_DIR", "./output/artifacts")
    )
