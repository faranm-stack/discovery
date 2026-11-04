"""Offline tests for correlation and privacy; no Azure log queries."""

import copy
import json

import pytest

from smoke_diagnostics import build_diagnostics, build_queries, write_diagnostics


def fixture():
    summary = {
        "run_id": "test-run", "project": "test-project", "agent": "test-agent",
        "started_at": "2026-10-21T02:00:00+00:00", "elapsed_seconds": 300,
        "response_id": "resp_test", "conversation_id": "conv_test",
        "submission_attempted": True, "phase": "complete", "verdict": "pass",
    }
    response = {"id": "resp_test", "status": "completed", "output": [
        {"type": "function_call", "id": "fc_test", "call_id": "call_test", "name": "tool_test", "arguments": "PRIVATE_ARGUMENT"},
        {"type": "function_call_output", "call_id": "call_test", "status": "completed", "output": "PRIVATE_OUTPUT"},
    ], "discoveryProperties": {"correlationId": "correlation_test", "itemExtensions": {
        "fc_test": {"supercomputerOperationId": "a" * 32},
    }}}
    operations = {"a" * 32: {"id": "a" * 32, "status": "Succeeded", "result": {
        "createdAt": "2026-10-21T02:02:00Z", "completedAt": "2026-10-21T02:03:00Z",
        "toolReport": {"logs": "PRIVATE_LOGS"},
    }}}
    return summary, response, operations


def test_links_response_call_and_operation_without_exporting_content():
    summary, response, operations = fixture()
    result = build_diagnostics(summary, response, operations)
    assert result["tool_calls"][0]["operation_id"] == "a" * 32
    assert result["tool_calls"][0]["execution_seconds"] == 60
    assert result["break_at"] is None
    serialized = json.dumps(result)
    assert all(secret not in serialized for secret in ["PRIVATE_ARGUMENT", "PRIVATE_OUTPUT", "PRIVATE_LOGS"])


@pytest.mark.parametrize("fault,expected", [
    ("failed_operation", "tool_execution"), ("unpaired", "tool_result"),
    ("no_operation", "operation_evidence"), ("no_call", "tool_selection"),
])
def test_identifies_where_evidence_chain_breaks(fault, expected):
    summary, response, operations = fixture()
    if fault == "failed_operation":
        operations["a" * 32]["status"] = "Failed"
    elif fault == "unpaired":
        response["output"].pop()
    elif fault == "no_operation":
        operations.clear()
    else:
        response["output"].clear()
    assert build_diagnostics(summary, response, operations)["break_at"] == expected


def test_queries_are_single_table_identifier_scoped_and_time_bounded():
    summary, response, operations = fixture()
    diagnostics = build_diagnostics(summary, response, operations)
    queries = build_queries(diagnostics)
    assert {q["table"] for q in queries} == {"DiscoveryLogs_CL", "DiscoveryToolLogs_CL", "KubeEvents_CL"}
    assert all("| take 100" in q["query"] for q in queries)
    assert all("join" not in q["query"] and "union" not in q["query"] for q in queries)
    assert diagnostics["timespan"] == "2026-10-21T01:55:00Z/2026-10-21T02:10:00Z"
    assert "correlation_test" in queries[0]["query"]
    assert "a" * 32 in queries[1]["query"]


def test_response_id_mismatch_and_query_injection_are_rejected():
    summary, response, operations = fixture()
    wrong = copy.deepcopy(response)
    wrong["id"] = "resp_other"
    with pytest.raises(ValueError):
        build_diagnostics(summary, wrong, operations)
    response["discoveryProperties"]["correlationId"] = "'; union *; //"
    with pytest.raises(ValueError):
        build_queries(build_diagnostics(summary, response, operations))


def test_offline_report_writes_queries_but_never_fetches_logs(tmp_path):
    summary, response, operations = fixture()
    for name, data in [("summary.json", summary), ("response.json", response), ("operations.json", operations)]:
        (tmp_path / name).write_text(json.dumps(data), encoding="utf-8")
    report = write_diagnostics(tmp_path)
    assert report["logs_fetched"] is False
    assert (tmp_path / "diagnostics.json").exists()
    assert len(list((tmp_path / "queries").glob("*.kql"))) == 3
    assert not (tmp_path / "log-evidence").exists()
    # Regenerating the offline report must retain an already collected manifest.
    (tmp_path / "log-evidence").mkdir()
    records = [{"http_status": 200, "error_code": None, "row_count": 1}]
    (tmp_path / "log-evidence/manifest.json").write_text(json.dumps(records), encoding="utf-8")
    regenerated = write_diagnostics(tmp_path)
    assert regenerated["logs_fetched"] is True
    assert regenerated["log_queries"] == records


def test_no_response_preflight_does_not_report_tool_failure():
    summary, _, _ = fixture()
    summary.update(mode="preflight", submission_attempted=False, phase="agent_preflight")
    result = build_diagnostics(summary, {}, {})
    assert result["break_at"] is None
    assert build_queries(result) == []