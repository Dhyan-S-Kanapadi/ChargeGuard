import base64
import json

import httpx
import pytest

from integrations.razorpay import RazorpayClient, RazorpayConfigError, RazorpayRequestError


def test_razorpay_client_loads_from_env() -> None:
    client = RazorpayClient.from_env(
        {
            "RAZORPAY_KEY_ID": "rzp_test_key",
            "RAZORPAY_KEY_SECRET": "secret",
        }
    )

    assert client.key_id == "rzp_test_key"
    assert client.key_secret == "secret"


def test_razorpay_client_requires_credentials() -> None:
    with pytest.raises(RazorpayConfigError):
        RazorpayClient.from_env({})


def test_razorpay_client_fetches_payment_with_basic_auth() -> None:
    expected_auth = "Basic " + base64.b64encode(b"rzp_test_key:secret").decode("ascii")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/payments/pay_123"
        assert request.headers["Authorization"] == expected_auth
        return httpx.Response(200, json={"id": "pay_123", "amount": 250000})

    client = RazorpayClient(
        key_id="rzp_test_key",
        key_secret="secret",
        client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.razorpay.com"),
    )

    payment = client.get_payment("pay_123")

    assert payment == {"id": "pay_123", "amount": 250000}


def test_razorpay_client_raises_for_error_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    client = RazorpayClient(
        key_id="rzp_test_key",
        key_secret="secret",
        client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.razorpay.com"),
    )

    with pytest.raises(RazorpayRequestError) as raised:
        client.get_order("order_123")
    assert raised.value.status_code == 401
    assert "unauthorized" not in str(raised.value)


def test_razorpay_client_verifies_with_read_only_payment_list() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/v1/payments"
        assert request.url.params["count"] == "1"
        return httpx.Response(200, json={"entity": "collection", "items": []})

    client = RazorpayClient(
        key_id="rzp_test_key",
        key_secret="secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    client.verify_credentials()


def test_razorpay_client_supports_dispute_lifecycle_requests() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/disputes":
            assert request.url.params["from"] == "100"
            assert request.url.params["to"] == "200"
            assert request.url.params["count"] == "50"
            assert request.url.params["skip"] == "10"
            return httpx.Response(200, json={"items": [{"id": "disp_1"}]})
        if request.url.path == "/v1/disputes/disp_1" and request.method == "GET":
            assert request.url.params["expand[]"] == "payment"
            return httpx.Response(200, json={"id": "disp_1", "status": "open"})
        if request.url.path.endswith("/accept"):
            assert request.method == "POST"
            return httpx.Response(200, json={"id": "disp_1", "status": "lost"})
        if request.url.path.endswith("/contest"):
            assert request.method == "PATCH"
            assert json.loads(request.content) == {
                "summary": "goods delivered",
                "shipping_proof": ["doc_1"],
                "action": "submit",
            }
            return httpx.Response(200, json={"id": "disp_1", "status": "under_review"})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = RazorpayClient(
        key_id="rzp_test_key",
        key_secret="secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.list_disputes(
        from_timestamp=100,
        to_timestamp=200,
        count=50,
        skip=10,
    ) == [{"id": "disp_1"}]
    assert client.get_dispute("disp_1", expand_payment=True)["status"] == "open"
    assert client.accept_dispute("disp_1")["status"] == "lost"
    assert client.contest_dispute(
        "disp_1",
        {"summary": "goods delivered", "shipping_proof": ["doc_1"]},
    )["status"] == "under_review"
    assert len(requests) == 4


def test_razorpay_client_fetches_expanded_card_and_uploads_document(tmp_path) -> None:
    document = tmp_path / "evidence.pdf"
    document.write_bytes(b"%PDF-test")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/payments/pay_123":
            assert request.url.params["expand[]"] == "card"
            return httpx.Response(
                200,
                json={"id": "pay_123", "method": "card", "card": {"network": "Visa"}},
            )
        assert request.url.path == "/v1/documents"
        assert request.method == "POST"
        assert "multipart/form-data" in request.headers["Content-Type"]
        assert b"dispute_evidence" in request.content
        assert b"%PDF-test" in request.content
        return httpx.Response(200, json={"id": "doc_1", "purpose": "dispute_evidence"})

    client = RazorpayClient(
        key_id="rzp_test_key",
        key_secret="secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.get_payment("pay_123", expand_card=True)["card"]["network"] == "Visa"
    assert client.upload_document(document)["id"] == "doc_1"
