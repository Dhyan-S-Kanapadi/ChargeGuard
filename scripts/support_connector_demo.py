"""Run the real FastAPI connector endpoints with local HTTP provider mocks.

Run from the repository root: python -m scripts.support_connector_demo
All metadata and encrypted credentials are temporary and removed on exit.
"""
import base64
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from functools import partial

import httpx
from cryptography.fernet import Fernet


def provider_response(request: httpx.Request) -> httpx.Response:
    """Only synthetic credentials are accepted; no outbound provider requests."""
    if request.url.host == "gmail.googleapis.com":
        credential = request.headers.get("Authorization", "").removeprefix("Bearer ")
        valid = "demo-gmail-token"
        body = {"messages": []}
    elif request.url.host == "demo.freshdesk.com":
        credential = base64.b64decode(request.headers.get("Authorization", "").removeprefix("Basic ")).decode().removesuffix(":X")
        valid = "demo-freshdesk-key"
        body = []
    else:
        return httpx.Response(403, json={"error": "demo_host_required"})
    if credential == "demo-unavailable-token":
        return httpx.Response(503, json={"error": "simulated_outage"})
    if credential != valid:
        return httpx.Response(401, json={"error": "synthetic_credentials_required"})
    return httpx.Response(200, json=body)


def main():
    if os.getenv("ENVIRONMENT", "development").lower() not in {"development", "test"}:
        raise SystemExit("The support demo runs only in development/test.")
    with TemporaryDirectory(prefix="chargeguard-support-demo-") as directory:
        os.environ.update(
            ENVIRONMENT="development", CHARGEGUARD_AUTH_MODE="operator",
            CHARGEGUARD_STORE_BACKEND="local", CHARGEGUARD_USE_STUBS="true",
            CHARGEGUARD_STORE_PATH=str(Path(directory) / "metadata.json"),
            CHARGEGUARD_CREDENTIAL_STORE_PATH=str(Path(directory) / "credentials.json"),
            CHARGEGUARD_CREDENTIAL_ENCRYPTION_KEY=Fernet.generate_key().decode(),
            API_KEY="support-demo-local-key", PUBLIC_DEMO_ENABLED="false",
            CHARGEGUARD_DEMO_SEED="false", RAZORPAY_RECOVER_PENDING_ON_STARTUP="false",
            RAZORPAY_SIMULATOR_ENABLED="false",
        )
        from core.runtime import EVIDENCE_PROVIDERS
        for name in EVIDENCE_PROVIDERS:
            os.environ[f"{name.upper()}_USE_STUBS"] = "true"
        import uvicorn
        from integrations import support_client_factory as factory
        from main import app

        with httpx.Client(transport=httpx.MockTransport(provider_response)) as provider:
            with patch.object(factory, "GmailReader", partial(factory.GmailReader, client=provider)), \
                 patch.object(factory, "FreshdeskClient", partial(factory.FreshdeskClient, client=provider)):
                uvicorn.run(app, host="127.0.0.1", port=8028)


if __name__ == "__main__":
    main()
