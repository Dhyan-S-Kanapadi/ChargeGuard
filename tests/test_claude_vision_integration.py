import base64
import json

import httpx

from integrations.claude_vision import ANTHROPIC_MESSAGES_URL, ClaudeVisionClient


def test_claude_vision_client_downloads_photo_and_requests_json_verification() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if str(request.url) == "https://cdn.example.test/pod.jpg":
            return httpx.Response(
                200,
                content=b"fake-image",
                headers={"content-type": "image/jpeg"},
            )
        if str(request.url) == ANTHROPIC_MESSAGES_URL:
            payload = json.loads(request.content)
            assert payload["temperature"] == 0
            assert payload["messages"][0]["content"][0]["source"]["media_type"] == "image/jpeg"
            assert (
                payload["messages"][0]["content"][0]["source"]["data"]
                == base64.b64encode(b"fake-image").decode("ascii")
            )
            return httpx.Response(
                200,
                json={
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {
                                    "delivered": True,
                                    "address_visible": True,
                                    "confidence": 0.91,
                                }
                            ),
                        }
                    ]
                },
            )
        return httpx.Response(404)

    client = ClaudeVisionClient(
        api_key="test_key",
        model="claude-test",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = client.verify_delivery_photo("https://cdn.example.test/pod.jpg")

    assert result == {
        "delivered": True,
        "address_visible": True,
        "confidence": 0.91,
    }
    assert [request.method for request in requests] == ["GET", "POST"]
