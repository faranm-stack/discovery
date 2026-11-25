import pytest
from run_catalog_smoke import pending_confirmations
from catalog_sweep import classify_execution


@pytest.mark.parametrize("status", ["Confirmed", "Approved", "Rejected", "Canceled"])
def test_settled_confirmation_does_not_remain_a_gate(status):
    response = {"discoveryProperties": {"confirmations": [{"status": status}]}}
    assert pending_confirmations(response) == []


@pytest.mark.parametrize("status", ["AwaitingUserInput", "Pending", "", "UnknownFutureState"])
def test_unresolved_confirmation_remains_human_controlled(status):
    response = {"id": "r", "status": "in_progress", "discoveryProperties": {"confirmations": [{"status": status}]}}
    assert pending_confirmations(response)
    result = classify_execution({"response_id": "r", "expected_tools": ["test"]}, response, {})
    assert result["status"] == "uncertain"
    assert result["reason"] == "confirmation_requires_human_review"


def test_confirmed_record_is_not_proof_of_success():
    response = {"id": "r", "status": "completed", "output": [],
                "discoveryProperties": {"confirmations": [{"status": "Confirmed"}]}}
    result = classify_execution({"response_id": "r", "expected_tools": ["test"]}, response, {})
    assert result["status"] == "failed"
    assert result["reason"] == "own_tool_not_called"