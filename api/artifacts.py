"""Authorized, short-lived access to private case artifacts."""
from datetime import datetime, timedelta, timezone
import os

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import Response

from api.auth import require_api_key
from api.disputes import _authorize_raw_access
from api.identity import verified_actor
from api.schemas import ArtifactDownloadGrant, ArtifactDownloadRedeem, ArtifactRetentionMark
from integrations.artifact_storage import ArtifactStorageError, artifact_storage


router = APIRouter(prefix="/disputes", tags=["artifacts"], dependencies=[Depends(require_api_key)])


def _grant_ttl_seconds() -> int:
    try:
        return min(600, max(60, int(os.getenv("ARTIFACT_DOWNLOAD_TTL_SECONDS", "300"))))
    except ValueError:
        return 300


def _merchant_for_case(chargeback_id: str) -> str:
    from api.store import store
    record = store.get_dispute(chargeback_id)
    if record is None:
        raise HTTPException(404, "Resource not found.")
    return record["state"].get("merchant_profile", {}).get("merchant_id") or ""


@router.post("/{chargeback_id}/artifacts/{artifact_id}/download-grant", response_model=ArtifactDownloadGrant)
def create_download_grant(
    chargeback_id: str,
    artifact_id: str,
    request: Request,
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
) -> ArtifactDownloadGrant:
    _authorize_raw_access(True, x_internal_token)
    from api.store import store
    merchant_id = _merchant_for_case(chargeback_id)
    artifact = store.get_case_artifact(merchant_id, chargeback_id, artifact_id)
    if artifact is None:
        raise HTTPException(404, "Resource not found.")
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=_grant_ttl_seconds())
    actor_id = verified_actor(request, "operator")
    result = store.create_artifact_download_grant(merchant_id, chargeback_id, artifact_id, actor_id, expires_at)
    if result is None:
        raise HTTPException(404, "Resource not found.")
    grant_id, token, _ = result
    return ArtifactDownloadGrant(grant_id=grant_id, token=token, expires_at=expires_at)


@router.post("/{chargeback_id}/artifacts/{artifact_id}/download/{grant_id}")
def download_artifact(
    chargeback_id: str,
    artifact_id: str,
    grant_id: str,
    payload: ArtifactDownloadRedeem,
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
) -> Response:
    _authorize_raw_access(True, x_internal_token)
    from api.store import store
    merchant_id = _merchant_for_case(chargeback_id)
    artifact = store.redeem_artifact_download_grant(grant_id, payload.token, merchant_id)
    if artifact is None or artifact["artifact_id"] != artifact_id or artifact["chargeback_id"] != chargeback_id:
        raise HTTPException(404, "Resource not found.")
    try:
        content = artifact_storage().read_verified(artifact["object_key"], artifact["sha256"])
    except ArtifactStorageError:
        raise HTTPException(404, "Resource not found.") from None
    return Response(content, media_type=artifact["content_type"], headers={
        "Cache-Control": "private, no-store",
        "Content-Disposition": 'attachment; filename="artifact"',
    })


@router.post("/{chargeback_id}/artifacts/{artifact_id}/retention")
def mark_retention(chargeback_id: str, artifact_id: str, payload: ArtifactRetentionMark) -> dict[str, str]:
    from api.store import store
    merchant_id = _merchant_for_case(chargeback_id)
    if not store.mark_artifact_retention(merchant_id, chargeback_id, artifact_id, payload.reason):
        raise HTTPException(404, "Resource not found.")
    return {"status": "retention_marked"}
