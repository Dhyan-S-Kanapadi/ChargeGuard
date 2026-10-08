from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from api.store import store
from core.runtime import RuntimeConfigurationError
from integrations.artifact_storage import (
    ArtifactImmutableError,
    ArtifactIntegrityError,
    ArtifactMissingError,
    LocalArtifactStorage,
    artifact_object_key,
    artifact_storage,
)


def _state(merchant_id: str = "merchant_artifact_a", chargeback_id: str = "case_artifact_a") -> dict:
    return {"chargeback_id": chargeback_id, "merchant_profile": {"merchant_id": merchant_id}}


def _artifact(merchant_id: str = "merchant_artifact_a", chargeback_id: str = "case_artifact_a") -> dict:
    state = _state(merchant_id, chargeback_id)
    if store.get_dispute(chargeback_id) is None:
        assert store.create_dispute(state)
    artifact_id = uuid4().hex
    storage = artifact_storage()
    key = artifact_object_key(merchant_id=merchant_id, chargeback_id=chargeback_id, artifact_id=artifact_id, filename="evidence.pdf")
    saved = storage.put_immutable(key, b"%PDF-artifact")
    artifact = {"artifact_id": artifact_id, "merchant_id": merchant_id, "chargeback_id": chargeback_id,
                "artifact_type": "evidence_attachment", "object_key": key, "content_type": "application/pdf",
                "size_bytes": saved.size_bytes, "sha256": saved.sha256}
    assert store.create_artifact(artifact)
    return artifact


def test_local_storage_scopes_keys_and_verifies_bytes(tmp_path) -> None:
    storage = LocalArtifactStorage(tmp_path)
    key = artifact_object_key(merchant_id="merchant_a", chargeback_id="case_a", artifact_id="artifact_a", filename="evidence.pdf")
    saved = storage.put_immutable(key, b"verified bytes")

    assert saved.object_key.startswith("merchants/merchant_a/cases/case_a/artifacts/artifact_a/")
    assert storage.read_verified(key, saved.sha256) == b"verified bytes"
    with pytest.raises(ArtifactImmutableError):
        storage.put_immutable(key, b"replacement bytes")
    (tmp_path / key).write_bytes(b"tampered")
    with pytest.raises(ArtifactIntegrityError):
        storage.read_verified(key, saved.sha256)
    with pytest.raises(ArtifactMissingError):
        storage.read_verified(key + ".missing", saved.sha256)


def test_production_storage_fails_closed_without_an_approved_provider(monkeypatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    with pytest.raises(RuntimeConfigurationError):
        artifact_storage()


def test_artifact_metadata_enforces_case_tenant_finalization_and_retention() -> None:
    artifact = _artifact()
    assert store.get_case_artifact("merchant_artifact_b", "case_artifact_a", artifact["artifact_id"]) is None
    assert store.finalize_artifacts("merchant_artifact_a", "case_artifact_a", [artifact["artifact_id"]])
    assert store.get_artifact(artifact["artifact_id"])["immutable_at"] is not None
    assert store.mark_artifact_retention("merchant_artifact_a", "case_artifact_a", artifact["artifact_id"], "legal_hold")
    retained = store.get_artifact(artifact["artifact_id"])
    assert retained["retention_reason"] == "legal_hold"
    assert retained["retention_marked_at"] is not None


def test_short_lived_grants_expire_and_are_single_use() -> None:
    artifact = _artifact()
    issued = store.create_artifact_download_grant(
        "merchant_artifact_a", "case_artifact_a", artifact["artifact_id"], "operator",
        datetime.now(timezone.utc) + timedelta(minutes=1),
    )
    assert issued is not None
    grant_id, token, _ = issued
    assert store.redeem_artifact_download_grant(grant_id, token, "merchant_artifact_a")["artifact_id"] == artifact["artifact_id"]
    assert store.redeem_artifact_download_grant(grant_id, token, "merchant_artifact_a") is None
    expired = store.create_artifact_download_grant(
        "merchant_artifact_a", "case_artifact_a", artifact["artifact_id"], "operator",
        datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    assert expired is not None
    assert store.redeem_artifact_download_grant(expired[0], expired[1], "merchant_artifact_a") is None


def test_download_route_requires_short_lived_grant_and_internal_raw_access(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("INTERNAL_API_TOKEN", "test-internal-token")
    monkeypatch.setenv("API_KEY", "test-api-key")
    monkeypatch.setenv("CHARGEGUARD_ARTIFACT_LOCAL_DIR", str(tmp_path))
    artifact = _artifact("merchant_download", "case_download")
    headers = {"X-Internal-Token": "test-internal-token"}
    from main import app
    with TestClient(app, headers={"X-API-Key": "test-api-key"}) as client:
        grant = client.post(
            f"/disputes/case_download/artifacts/{artifact['artifact_id']}/download-grant", headers=headers
        )
        assert grant.status_code == 200
        body = grant.json()
        response = client.post(
            f"/disputes/case_download/artifacts/{artifact['artifact_id']}/download/{body['grant_id']}",
            headers=headers, json={"token": body["token"]},
        )
        assert response.status_code == 200
        assert response.content == b"%PDF-artifact"
        assert response.headers["cache-control"] == "private, no-store"
        assert client.post(
            f"/disputes/case_download/artifacts/{artifact['artifact_id']}/download/{body['grant_id']}",
            headers=headers, json={"token": body["token"]},
        ).status_code == 404
        assert client.get(
            f"/disputes/case_download/artifacts/{artifact['artifact_id']}/download/{body['grant_id']}?token={body['token']}",
            headers=headers,
        ).status_code == 405
        assert client.post(
            f"/disputes/case_download/artifacts/{artifact['artifact_id']}/download-grant"
        ).status_code == 403
