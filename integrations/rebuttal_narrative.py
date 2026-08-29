"""Optional, bounded narrative generation for rebuttal packets."""

import os
from collections.abc import Mapping
from typing import Any

import httpx

from integrations.claude_vision import ANTHROPIC_MESSAGES_URL, ANTHROPIC_VERSION


class RebuttalNarrativeConfigError(RuntimeError):
    """Raised when rebuttal-narrative credentials are missing."""


class RebuttalNarrativeRequestError(RuntimeError):
    """Raised when a rebuttal-narrative request fails or is malformed."""


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def rebuttal_narrative_enabled() -> bool:
    return _env_flag("REBUTTAL_NARRATIVE_ENABLED")


def rebuttal_narrative_uses_stubs() -> bool:
    value = os.getenv("REBUTTAL_NARRATIVE_USE_STUBS")
    if value is None:
        return _env_flag("CHARGEGUARD_USE_STUBS")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def narrative_facts(packet: dict[str, Any]) -> dict[str, Any]:
    """Return only existing rebuttal facts, never raw evidence payloads."""
    return {
        "evidence_status": packet.get("evidence_status", {}),
        "strongest_evidence": packet.get("strongest_evidence", []),
        "sections": packet.get("sections", []),
    }


class RebuttalNarrativeClient:
    """Anthropic client for a non-authoritative rebuttal cover summary."""

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
    ) -> "RebuttalNarrativeClient":
        values = env or os.environ
        api_key = values.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RebuttalNarrativeConfigError("ANTHROPIC_API_KEY is required.")
        return cls(api_key=api_key, model=values.get("REBUTTAL_NARRATIVE_MODEL", "claude-sonnet-5"))

    def generate_narrative(self, facts: dict[str, Any]) -> str:
        payload = {
            "model": self.model,
            "max_tokens": 450,
            "temperature": 0,
            "system": (
                "Write a formal, professional card-network rebuttal cover summary in 2-3 short "
                "paragraphs. Rephrase and organize only facts supplied in evidence_status, "
                "strongest_evidence, and sections. Never invent any claim, statistic, or detail. "
                "When evidence is thin, state that plainly. Do not use first person or marketing "
                "language. Return only the narrative text."
            ),
            "messages": [{"role": "user", "content": str(facts)}],
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
            raise RebuttalNarrativeRequestError(
                f"Rebuttal narrative request failed with {response.status_code}: {response.text}"
            )
        data = response.json()
        if not isinstance(data, dict):
            raise RebuttalNarrativeRequestError("Rebuttal narrative API response was not an object.")
        return data

    @staticmethod
    def _extract_text(response: dict[str, Any]) -> str:
        content = response.get("content")
        if not isinstance(content, list):
            raise RebuttalNarrativeRequestError("Rebuttal narrative response did not include content.")
        text = "".join(
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ).strip()
        if not text:
            raise RebuttalNarrativeRequestError("Rebuttal narrative response text was empty.")
        return text


def _stub_narrative(facts: dict[str, Any]) -> str:
    highlights = facts["strongest_evidence"]
    if highlights:
        return "The submitted record includes " + ", ".join(highlights) + "."
    return "The submitted record contains only the evidence identified in the attached sections."


def generate_rebuttal_narrative(packet: dict[str, Any]) -> str:
    facts = narrative_facts(packet)
    if rebuttal_narrative_uses_stubs():
        return _stub_narrative(facts)
    return RebuttalNarrativeClient.from_env().generate_narrative(facts)
