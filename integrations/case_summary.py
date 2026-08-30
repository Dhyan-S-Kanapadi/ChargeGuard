"""Bounded, optional plain-English summaries for human case review."""

import json
import os
from collections.abc import Mapping
from typing import Any

import httpx

from core.state import ChargebackState
from integrations.claude_vision import ANTHROPIC_MESSAGES_URL, ANTHROPIC_VERSION


class CaseSummaryConfigError(RuntimeError):
    """Raised when case-summary credentials are missing."""


class CaseSummaryRequestError(RuntimeError):
    """Raised when the case-summary request fails or has an invalid response."""


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def case_summary_uses_stubs() -> bool:
    value = os.getenv("CASE_SUMMARY_USE_STUBS")
    if value is None:
        return _env_flag("CHARGEGUARD_USE_STUBS")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def case_summary_facts(state: ChargebackState) -> dict[str, Any]:
    """Return the narrow, already-computed facts allowed into the prompt."""
    evidence_status = {
        name: bool(state.get(name))
        for name in (
            "transaction",
            "shipping",
            "comms",
            "device",
            "consortium",
            "delivery_photo",
            "order_timeline",
        )
    }
    return {
        "chargeback_id": state["chargeback_id"],
        "win_probability": state.get("win_probability"),
        "expected_value": state.get("expected_value"),
        "third_party_fraud_indicators": state.get("third_party_fraud_indicators"),
        "identity_continuity": state.get("identity_continuity"),
        "contradiction_flags": state.get("contradiction_flags", []),
        "degraded_reasons": state.get("degraded_reasons", []),
        "decision_reasoning": state.get("decision_reasoning"),
        "requires_human_review": state.get("requires_human_review"),
        "evidence_status": evidence_status,
    }


class CaseSummaryClient:
    """Anthropic client for bounded reviewer-facing prose."""

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
    def from_env(cls, env: Mapping[str, str] | None = None) -> "CaseSummaryClient":
        values = env or os.environ
        api_key = values.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise CaseSummaryConfigError("ANTHROPIC_API_KEY is required.")
        return cls(api_key=api_key, model=values.get("CASE_SUMMARY_MODEL", "claude-sonnet-5"))

    def summarize_case(self, facts: dict[str, Any]) -> str:
        payload = {
            "model": self.model,
            "max_tokens": 300,
            "temperature": 0,
            "system": (
                "You produce a plain-English summary for a chargeback human reviewer. "
                "Use only the supplied facts. Never invent, infer, estimate, or name evidence "
                "not present in those facts. Write 2-4 concise sentences covering available "
                "evidence, missing or degraded evidence, and why a human must decide. "
                "The decision_reasoning field states the actual system-level reason for escalation "
                "(for example, a missing or failed scoring model) separately from evidence collection "
                "status in degraded_reasons and evidence_status; do not conflate the two, and state "
                "clearly which one applies to this case. "
                "Return only the summary text, not JSON or headings."
            ),
            "tools": [
                {
                    "name": "return_case_summary",
                    "description": "Return the bounded human-review case summary.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "summary": {"type": "string", "minLength": 1},
                        },
                        "required": ["summary"],
                        "additionalProperties": False,
                    },
                }
            ],
            "tool_choice": {"type": "tool", "name": "return_case_summary"},
            "messages": [{"role": "user", "content": json.dumps(facts, default=str)}],
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
            raise CaseSummaryRequestError(
                f"Case summary request failed with {response.status_code}: {response.text}"
            )
        data = response.json()
        if not isinstance(data, dict):
            raise CaseSummaryRequestError("Case summary API response was not an object.")
        return data

    @staticmethod
    def _extract_text(response: dict[str, Any]) -> str:
        content = response.get("content")
        if not isinstance(content, list):
            raise CaseSummaryRequestError("Case summary response did not include content.")
        for item in content:
            if isinstance(item, dict) and item.get("type") == "tool_use":
                summary = item.get("input", {}).get("summary")
                if isinstance(summary, str) and summary.strip():
                    return summary.strip()
        text = "".join(
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ).strip()
        if not text:
            raise CaseSummaryRequestError("Case summary response text was empty.")
        return text


def _stub_summary(facts: dict[str, Any]) -> str:
    evidence = facts["evidence_status"]
    available = ", ".join(name for name, present in evidence.items() if present) or "no evidence"
    degraded_reasons = facts.get("degraded_reasons") or []
    reasoning = facts.get("decision_reasoning") or ""

    if degraded_reasons:
        cause = f"evidence collection was degraded because of {', '.join(degraded_reasons)}"
    elif "model_unavailable" in reasoning or "model_error" in reasoning:
        cause = "the win-probability model was unavailable or failed to run"
    else:
        cause = "the automated decision could not be made with confidence"
    return (
        f"Available evidence includes {available}. This case required escalation because "
        f"{cause}, so a human reviewer must assess the existing record before proceeding."
    )


def generate_case_summary(state: ChargebackState) -> str:
    """Generate optional prose without modifying any deterministic state fields."""
    facts = case_summary_facts(state)
    if case_summary_uses_stubs():
        return _stub_summary(facts)
    return CaseSummaryClient.from_env().summarize_case(facts)
