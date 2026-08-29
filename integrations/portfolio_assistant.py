"""Grounded, read-only portfolio question answering."""

import os
from collections.abc import Mapping
from typing import Any

import httpx

from integrations.claude_vision import ANTHROPIC_MESSAGES_URL, ANTHROPIC_VERSION


class PortfolioAssistantConfigError(RuntimeError):
    """Raised when portfolio-assistant credentials are missing."""


class PortfolioAssistantRequestError(RuntimeError):
    """Raised when a portfolio-assistant request fails or is malformed."""


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def portfolio_assistant_uses_stubs() -> bool:
    value = os.getenv("PORTFOLIO_ASSISTANT_USE_STUBS")
    if value is None:
        return _env_flag("CHARGEGUARD_USE_STUBS")
    return value.strip().lower() in {"1", "true", "yes", "on"}


class PortfolioAssistantClient:
    """Anthropic client for answers grounded in supplied portfolio context."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "claude-sonnet-5",
        timeout: float = 20.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._client = client

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
    ) -> "PortfolioAssistantClient":
        values = env or os.environ
        api_key = values.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise PortfolioAssistantConfigError("ANTHROPIC_API_KEY is required.")
        return cls(api_key=api_key, model=values.get("PORTFOLIO_ASSISTANT_MODEL", "claude-sonnet-5"))

    def answer(self, question: str, context: dict[str, Any]) -> str:
        payload = {
            "model": self.model,
            "max_tokens": 500,
            "temperature": 0,
            "system": (
                "Answer only from the supplied ChargeGuard portfolio context. If information is "
                "not in that context, say so plainly and do not guess. Do not recommend a new "
                "fight or accept decision for an unresolved chargeback; instead state the existing "
                "system decision and deterministic reasoning when available. Return only a concise "
                "plain-English answer."
            ),
            "messages": [
                {"role": "user", "content": str({"question": question, "context": context})}
            ],
        }
        if self._client is not None:
            response = self._send_request(self._client, payload)
        else:
            with httpx.Client(timeout=self.timeout) as client:
                response = self._send_request(client, payload)
        return self._extract_text(response)

    def _send_request(self, client: httpx.Client, payload: dict[str, Any]) -> dict[str, Any]:
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
            raise PortfolioAssistantRequestError(
                f"Portfolio assistant request failed with {response.status_code}: {response.text}"
            )
        data = response.json()
        if not isinstance(data, dict):
            raise PortfolioAssistantRequestError("Portfolio assistant API response was not an object.")
        return data

    @staticmethod
    def _extract_text(response: dict[str, Any]) -> str:
        content = response.get("content")
        if not isinstance(content, list):
            raise PortfolioAssistantRequestError("Portfolio assistant response did not include content.")
        text = "".join(
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ).strip()
        if not text:
            raise PortfolioAssistantRequestError("Portfolio assistant response text was empty.")
        return text


def _stub_answer(context: dict[str, Any]) -> str:
    requested = context.get("requested_chargeback_id")
    if requested and not context.get("requested_chargeback_found"):
        return f"Chargeback {requested} is not present in the supplied portfolio context."
    count = len(context.get("disputes", []))
    return f"This answer is grounded in the current statistics and {count} supplied dispute summaries."


def generate_portfolio_answer(question: str, context: dict[str, Any]) -> str:
    if portfolio_assistant_uses_stubs():
        return _stub_answer(context)
    return PortfolioAssistantClient.from_env().answer(question, context)
