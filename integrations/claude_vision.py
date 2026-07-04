import base64
import json
import mimetypes
import os
from collections.abc import Mapping
from typing import Any

import httpx


ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"


class ClaudeVisionConfigError(RuntimeError):
    """Raised when Claude Vision credentials are missing."""


class ClaudeVisionRequestError(RuntimeError):
    """Raised when Claude Vision cannot verify an image."""


class ClaudeVisionClient:
    """Claude Vision client for proof-of-delivery photo verification."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "claude-3-5-sonnet-latest",
        timeout: float = 20.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._client = client

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "ClaudeVisionClient":
        values = env or os.environ
        api_key = values.get("ANTHROPIC_API_KEY")
        model = values.get("ANTHROPIC_VISION_MODEL", "claude-3-5-sonnet-latest")

        if not api_key:
            raise ClaudeVisionConfigError("ANTHROPIC_API_KEY is required.")

        return cls(api_key=api_key, model=model)

    def verify_delivery_photo(self, photo_url: str) -> dict[str, Any]:
        image_bytes, media_type = self._download_image(photo_url)
        response = self._post_messages(image_bytes, media_type)
        text = self._extract_text(response)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ClaudeVisionRequestError("Claude Vision response was not valid JSON.") from exc

        if not isinstance(parsed, dict):
            raise ClaudeVisionRequestError("Claude Vision JSON response was not an object.")
        return parsed

    def _download_image(self, photo_url: str) -> tuple[bytes, str]:
        if not photo_url:
            raise ClaudeVisionRequestError("photo_url is required.")

        if self._client is not None:
            return self._send_image_request(self._client, photo_url)

        with httpx.Client(timeout=self.timeout) as client:
            return self._send_image_request(client, photo_url)

    def _send_image_request(self, client: httpx.Client, photo_url: str) -> tuple[bytes, str]:
        response = client.get(photo_url)
        if response.status_code >= 400:
            raise ClaudeVisionRequestError(
                f"Photo download failed with {response.status_code}: {response.text}"
            )

        content_type = response.headers.get("content-type", "").split(";")[0].strip()
        guessed_type = mimetypes.guess_type(photo_url)[0]
        media_type = content_type or guessed_type or "image/jpeg"
        return response.content, media_type

    def _post_messages(self, image_bytes: bytes, media_type: str) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "max_tokens": 300,
            "temperature": 0,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": base64.b64encode(image_bytes).decode("ascii"),
                            },
                        },
                        {
                            "type": "text",
                            "text": (
                                "Does this photo show a successful delivery at a residential "
                                "address? Is an address or door visible? Respond only JSON with "
                                "keys delivered, address_visible, confidence."
                            ),
                        },
                    ],
                }
            ],
        }

        if self._client is not None:
            return self._send_message_request(self._client, payload)

        with httpx.Client(timeout=self.timeout) as client:
            return self._send_message_request(client, payload)

    def _send_message_request(
        self,
        client: httpx.Client,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        response = client.post(
            ANTHROPIC_MESSAGES_URL,
            json=payload,
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
        )
        if response.status_code >= 400:
            raise ClaudeVisionRequestError(
                f"Claude Vision request failed with {response.status_code}: {response.text}"
            )
        data = response.json()
        if not isinstance(data, dict):
            raise ClaudeVisionRequestError("Claude Vision API response was not an object.")
        return data

    @staticmethod
    def _extract_text(response: dict[str, Any]) -> str:
        content = response.get("content")
        if not isinstance(content, list):
            raise ClaudeVisionRequestError("Claude Vision response did not include content.")

        text_parts = [
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        text = "".join(text_parts).strip()
        if not text:
            raise ClaudeVisionRequestError("Claude Vision response text was empty.")
        return text
