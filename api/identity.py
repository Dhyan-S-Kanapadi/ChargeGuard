"""Verified Supabase identity plus explicit, deny-by-default route permissions."""
from functools import lru_cache
import os
from typing import Literal
from uuid import UUID

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from starlette.concurrency import run_in_threadpool

from core.identity import auth_mode, supabase_url, validate_identity_configuration
from db.identity import IdentityStore


def identity_store():
    from api.store import store
    if not hasattr(store, "database_url"):
        raise HTTPException(503, "Production identity requires PostgreSQL.")
    return IdentityStore(store)


@lru_cache(maxsize=2)
def signing_keys(url):
    return jwt.PyJWKClient(url + "/auth/v1/.well-known/jwks.json", timeout=5, lifespan=300)


def verify_token(token):
    url = supabase_url()
    try:
        if len(token) > 16384:
            raise ValueError()
        header = jwt.get_unverified_header(token)
        if header.get("alg") not in {"ES256", "RS256"} or not isinstance(header.get("kid"), str):
            raise ValueError()
        key = signing_keys(url).get_signing_key_from_jwt(token)
        claims = jwt.decode(token, key.key, algorithms=["ES256", "RS256"],
                            audience="authenticated", issuer=url + "/auth/v1",
                            options={"require": ["exp", "iat", "sub", "iss", "aud", "session_id", "aal"]})
        if (claims.get("role") != "authenticated" or claims.get("is_anonymous") is not False
                or claims["aal"] not in {"aal1", "aal2"}
                or not isinstance(claims["sub"], str) or not isinstance(claims["session_id"], str)
                or type(claims["iat"]) is not int or type(claims["exp"]) is not int
                or not 0 < claims["exp"] - claims["iat"] <= 600):
            raise ValueError()
        claims["sub"] = str(UUID(claims["sub"]))
        claims["session_id"] = str(UUID(claims["session_id"]))
        return claims
    except jwt.PyJWKClientConnectionError:
        raise HTTPException(503, "Identity verification is temporarily unavailable.") from None
    except (jwt.PyJWTError, ValueError, TypeError, KeyError):
        raise HTTPException(401, "Invalid or expired login token.", headers={"WWW-Authenticate": "Bearer"}) from None


# Route templates, not path prefixes. New routes are denied until explicitly reviewed.
READ_ROUTES = {
    "/merchants/{merchant_id}/support-connectors",
    "/disputes", "/disputes/{chargeback_id}", "/disputes/{chargeback_id}/summary",
    "/merchants", "/merchants/{merchant_id}", "/stats", "/assistant/status",
    "/merchants/{merchant_id}/payment-connectors", "/merchants/{merchant_id}/shipping-connectors", "/merchants/{merchant_id}/device-risk-connectors",
}
OWNER_WRITES = {
    ("POST", "/merchants/{merchant_id}/support-connectors/gmail"),
    ("POST", "/merchants/{merchant_id}/support-connectors/freshdesk"),
    ("POST", "/merchants/{merchant_id}/support-connectors/{connector_id}/verify"),
    ("DELETE", "/merchants/{merchant_id}/support-connectors/{connector_id}"),
    ("PATCH", "/merchants/{merchant_id}"), ("POST", "/merchants/{merchant_id}/sync-shopify-history"),
    ("POST", "/orders/ingest"), ("POST", "/webhook/chargeback"),
    ("POST", "/merchants/{merchant_id}/payment-connectors/razorpay"),
    ("POST", "/merchants/{merchant_id}/payment-connectors/stripe"),
    ("POST", "/merchants/{merchant_id}/payment-connectors/{connector_id}/verify"),
    ("DELETE", "/merchants/{merchant_id}/payment-connectors/{connector_id}"),
    ("POST", "/merchants/{merchant_id}/shipping-connectors/shiprocket"),
    ("POST", "/merchants/{merchant_id}/shipping-connectors/delhivery"),
    ("POST", "/merchants/{merchant_id}/shipping-connectors/{connector_id}/verify"),
    ("DELETE", "/merchants/{merchant_id}/shipping-connectors/{connector_id}"),
    ("POST", "/merchants/{merchant_id}/device-risk-connectors/seon"),
    ("POST", "/merchants/{merchant_id}/device-risk-connectors/{connector_id}/verify"),
    ("DELETE", "/merchants/{merchant_id}/device-risk-connectors/{connector_id}"),
}
REVIEW_WRITES = {
    ("POST", "/disputes/{chargeback_id}/classification/suggestion"),
    ("POST", "/disputes/{chargeback_id}/classification/suggestion/reject"),
    ("POST", "/disputes/{chargeback_id}/classification"),
    ("POST", "/disputes/{chargeback_id}/outcome"),
}
ADMIN_ROUTES = {
    ("POST", "/merchants"), ("GET", "/internal/runtime"),
    ("GET", "/internal/razorpay/events"), ("POST", "/internal/razorpay/events/{event_id}/retry"),
    ("POST", "/internal/razorpay/process-pending"), ("POST", "/internal/razorpay/reconcile"),
    ("POST", "/auth/users/{user_id}"),
}
SELF_ROUTES = {("GET", "/auth/me"), ("POST", "/auth/session/revoke")}
MEMBER_ROUTE = "/auth/merchants/{merchant_id}/members/{user_id}"


def authorize_route(request, principal, body):
    from api.store import store
    route = request.scope["route"].path
    action = (request.method, route)
    if action in SELF_ROUTES:
        return None
    if principal.platform_admin:
        if principal.aal == "aal2" and (action in ADMIN_ROUTES or (route == MEMBER_ROUTE and request.method in {"POST", "DELETE"})):
            return request.path_params.get("merchant_id")
        raise HTTPException(403, "Platform administrators use separate administration routes and MFA.")
    if principal.aal != "aal2" and any(role in {"owner", "reviewer"} for role in principal.memberships.values()):
        raise HTTPException(403, "MFA is required for owners and reviewers.")
    if action in ADMIN_ROUTES:
        raise HTTPException(403, "Platform administrator required.")
    roles = {"owner", "reviewer", "read_only"}
    if action in OWNER_WRITES or (route == MEMBER_ROUTE and request.method in {"POST", "DELETE"}):
        roles = {"owner"}
    elif action in REVIEW_WRITES:
        roles = {"owner", "reviewer"}
    elif not ((request.method == "GET" and route in READ_ROUTES) or action == ("POST", "/assistant/query")):
        raise HTTPException(403, "Route is not enabled for merchant accounts.")
    merchant = request.path_params.get("merchant_id")
    if route in {"/orders/ingest", "/webhook/chargeback"}:
        merchant = body.get("merchant_id")
        if not isinstance(merchant, str) or not merchant:
            raise HTTPException(422, "merchant_id is required.")
    case = request.path_params.get("chargeback_id") or (body.get("chargeback_id") if route == "/assistant/query" else None)
    if case:
        if not isinstance(case, str):
            raise HTTPException(422, "Invalid case identifier.")
        record = store.get_dispute(case)
        merchant = record["state"].get("merchant_profile", {}).get("merchant_id") if record else None
        if not merchant:
            raise HTTPException(404, "Resource not found.")
    if merchant is not None and principal.memberships.get(merchant) not in roles:
        # Same response for missing and foreign resources; no existence oracle.
        raise HTTPException(404, "Resource not found.")
    for requested in request.query_params.getlist("merchant_id"):
        if requested not in principal.memberships:
            raise HTTPException(404, "Resource not found.")
    if request.query_params.get("include_raw", "false").lower() not in {"false", "0", "off", "no"}:
        if principal.memberships.get(merchant) != "owner":
            raise HTTPException(403, "Raw evidence requires merchant owner permission and the internal token.")
    return merchant


async def authenticate_request(request, authorization):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Supabase login required.", headers={"WWW-Authenticate": "Bearer"})
    claims = await run_in_threadpool(verify_token, authorization[7:])
    repository = identity_store()
    principal = await run_in_threadpool(repository.authenticate, claims)
    request.state.principal = principal
    body = {}
    if request.method in {"POST", "PATCH", "PUT"}:
        try:
            body = await request.json()
        except ValueError:
            pass  # Let the endpoint schema reject malformed bodies after authorization.
        if not isinstance(body, dict):
            body = {}
    action = request.method + " " + request.scope["route"].path
    try:
        merchant = await run_in_threadpool(authorize_route, request, principal, body)
    except HTTPException:
        await run_in_threadpool(repository.audit, principal, action, "denied")
        raise
    # Fail closed if the authorization audit cannot be committed before an effect.
    await run_in_threadpool(repository.audit, principal, action, "authorized", merchant)
    return principal.user_id  # Rate-limit by user, never by the raw bearer token.


def visible_disputes(request, records):
    principal = getattr(request.state, "principal", None)
    if principal is None:
        if auth_mode() == "supabase":
            raise HTTPException(401, "Login required.")
        return records
    selected = request.query_params.get("merchant_id")
    return [record for record in records
            if (merchant := record["state"].get("merchant_profile", {}).get("merchant_id")) in principal.memberships
            and (not selected or merchant == selected)]


def verified_actor(request, supplied):
    principal = getattr(request.state, "principal", None)
    return principal.user_id if principal else supplied


from api.auth import require_api_key  # Imported after helpers to avoid dependency cycle.

router = APIRouter(prefix="/auth", tags=["identity"])


@router.get("/config")
def configuration():
    if auth_mode() != "supabase":
        return {"mode": "operator"}
    validate_identity_configuration()
    return {"mode": "supabase", "url": supabase_url(), "publishable_key": os.environ["SUPABASE_PUBLISHABLE_KEY"]}


@router.get("/me", dependencies=[Depends(require_api_key)])
def current_user(request: Request):
    principal = getattr(request.state, "principal", None)
    if principal is None:
        raise HTTPException(404, "Merchant identity is not enabled.")
    return {"user_id": principal.user_id, "aal": principal.aal,
            "platform_admin": principal.platform_admin, "memberships": principal.memberships}


@router.post("/session/revoke", dependencies=[Depends(require_api_key)])
def revoke(request: Request):
    principal = getattr(request.state, "principal", None)
    if principal is None:
        raise HTTPException(404, "Merchant identity is not enabled.")
    identity_store().revoke_session(principal)
    return {"status": "revoked"}


class AccountStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    active: bool = True


class Membership(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["owner", "reviewer", "read_only"]


@router.post("/users/{user_id}", dependencies=[Depends(require_api_key)])
def provision(request: Request, user_id: UUID, payload: AccountStatus):
    if not getattr(request.state, "principal", None):
        raise HTTPException(403, "Production identity required.")
    identity_store().provision_user(request.state.principal, str(user_id), supabase_url() + "/auth/v1", payload.active)
    return {"status": "updated"}


@router.post("/merchants/{merchant_id}/members/{user_id}", dependencies=[Depends(require_api_key)])
def set_membership(request: Request, merchant_id: str, user_id: UUID, payload: Membership):
    if not getattr(request.state, "principal", None):
        raise HTTPException(403, "Production identity required.")
    identity_store().membership(request.state.principal, merchant_id, str(user_id), payload.role)
    return {"status": "updated"}


@router.delete("/merchants/{merchant_id}/members/{user_id}", dependencies=[Depends(require_api_key)])
def remove_membership(request: Request, merchant_id: str, user_id: UUID):
    if not getattr(request.state, "principal", None):
        raise HTTPException(403, "Production identity required.")
    identity_store().membership(request.state.principal, merchant_id, str(user_id), None)
    return {"status": "removed"}
