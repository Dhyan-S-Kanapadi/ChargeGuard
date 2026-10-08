import logging
from datetime import datetime, timezone

from core.state import ChargebackState
from core.runtime import assert_workflow_environment, runtime_environment
from integrations.artifact_storage import ArtifactStorageError, artifact_storage


logger = logging.getLogger(__name__)


def _decision_log_extra(state: ChargebackState) -> dict[str, object]:
    return {
        "chargeback_id": state["chargeback_id"],
        "decision": state.get("decision"),
        "win_probability": state.get("win_probability"),
        "expected_value": state.get("expected_value"),
        "dispute_amount": state["dispute_amount"],
        "currency": state["currency"],
    }


def _confirmation_id(state: ChargebackState, filed_at: datetime) -> str:
    timestamp = filed_at.strftime("%Y%m%d%H%M%S")
    return f"filed_{state['card_network'].lower()}_{state['chargeback_id']}_{timestamp}"


def filing_agent(state: ChargebackState) -> ChargebackState:
    """Record a filing confirmation for the prepared rebuttal."""
    assert_workflow_environment(state)
    if runtime_environment() == "production":
        state["filed_at"] = None
        state["filing_confirmation"] = "filing_blocked_production_adapter_unavailable"
        state["final_outcome"] = "PENDING"
        return state
    if not state.get("quality_approved"):
        state["filing_confirmation"] = "filing_blocked_quality_not_approved"
        return state

    artifact_id = state.get("rebuttal_artifact_id")
    if not artifact_id:
        state["filing_confirmation"] = "filing_blocked_missing_rebuttal"
        return state
    from api.store import store
    artifact = store.get_case_artifact(
        state["merchant_profile"]["merchant_id"], state["chargeback_id"], artifact_id
    )
    if not artifact or not artifact.get("immutable_at"):
        state["filing_confirmation"] = "filing_blocked_rebuttal_not_immutable"
        return state
    try:
        artifact_storage().read_verified(artifact["object_key"], artifact["sha256"])
    except ArtifactStorageError:
        state["filing_confirmation"] = "filing_blocked_missing_rebuttal"
        return state

    filed_at = datetime.now(timezone.utc)
    state["filed_at"] = filed_at
    state["filing_confirmation"] = _confirmation_id(state, filed_at)
    logger.info(
        "Running filing agent for %s",
        state["chargeback_id"],
        extra=_decision_log_extra(state),
    )
    return state
