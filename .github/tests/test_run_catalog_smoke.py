"""Deterministic evidence checks; these tests never invoke an agent."""

import asyncio
import copy
import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlencode, quote, urlsplit

import pytest
import yaml

import run_catalog_smoke
from run_catalog_smoke import SmokeError, assess_response, validate_endpoint


MARKER = "catalog-smoke-test-123"


def test_studio_opens_populated_conversation_by_parent_resource_name():
    from unittest.mock import AsyncMock
    client = AsyncMock()
    client.create_conversation.return_value = "populated-id"
    report = {"investigation": "agent-run-123", "display_name": "Agent Nov 23"}
    assert asyncio.run(run_catalog_smoke.create_studio_thread(client, "p", report)) == "populated-id"
    assert client.create_investigation.await_args.kwargs["display_name"] == "Agent Nov 23"
    client.create_conversation.assert_awaited_once_with("p", "agent-run-123", display_name="agent-run-123")


def completed_response():
    return {
        "id": "response-test",
        "status": "completed",
        "output": [
            {
                "type": "function_call",
                "call_id": "call-1",
                "name": "execute_corepython",
                "arguments": "{}",
            },
            {
                "type": "function_call_output",
                "call_id": "call-1",
                "output": json.dumps({
                    "status": "Succeeded",
                    "exit_code": 0,
                    "stdout": json.dumps({"smoke_marker": MARKER, "value": 42}),
                }),
            },
        ],
    }


def assess(response):
    return assess_response(response, expected_tool="corepython", marker=MARKER)


def test_pass_requires_correlated_tool_output_and_computed_result():
    result = assess(completed_response())
    assert result["verdict"] == "pass"
    assert result["paired_tool_results"] == 1
    assert result["expected_tool_called"] is True


@pytest.mark.parametrize("status", ["failed", "incomplete", "cancelled", "in_progress"])
def test_noncompleted_response_cannot_pass(status):
    response = completed_response()
    response["status"] = status
    assert assess(response)["verdict"] != "pass"


def test_final_answer_without_tool_execution_is_inconclusive():
    response = {"status": "completed", "output": [{
        "type": "message", "role": "assistant",
        "content": [{"type": "output_text", "text": f"{MARKER}: 42. Success!"}],
    }]}
    assert assess(response)["verdict"] == "inconclusive"


def test_submission_receipt_is_not_execution_success():
    response = completed_response()
    response["output"][1]["output"] = {"status": "Queued", "jobId": "job-1"}
    assert assess(response)["verdict"] == "inconclusive"


@pytest.mark.parametrize("change", ["missing_result", "wrong_id", "duplicate", "no_id"])
def test_unmatched_or_ambiguous_call_ids_cannot_pass(change):
    response = completed_response()
    if change == "missing_result":
        response["output"].pop()
    elif change == "wrong_id":
        response["output"][1]["call_id"] = "unrelated"
    elif change == "duplicate":
        response["output"].append(copy.deepcopy(response["output"][1]))
    else:
        del response["output"][0]["call_id"]
    assert assess(response)["verdict"] != "pass"


@pytest.mark.parametrize("patch", [
    {"exit_code": 1}, {"exit_code": False}, {"exit_code": None},
    {"status": "Failed"}, {"status": "Running"}, {"error": {"code": "ToolFailed"}},
    {"stdout": json.dumps({"smoke_marker": "old-run", "value": 42})},
    {"stdout": json.dumps({"smoke_marker": MARKER, "value": 41})},
])
def test_tool_errors_missing_exit_or_wrong_result_cannot_pass(patch):
    response = completed_response()
    output = json.loads(response["output"][1]["output"])
    output.update(patch)
    response["output"][1]["output"] = output
    assert assess(response)["verdict"] != "pass"


def test_unrelated_tool_cannot_satisfy_pilot():
    response = completed_response()
    response["output"][0]["name"] = "different_tool"
    response["output"][0]["arguments"] = json.dumps({"code": "# corepython"})
    assert assess(response)["verdict"] == "inconclusive"


def test_explicit_tool_id_and_separate_job_result_are_supported():
    response = completed_response()
    response["output"][0].update({
        "name": "ExecuteTool",
        "arguments": json.dumps({"toolId": "/subscriptions/test/providers/Microsoft.Discovery/tools/corepython"}),
    })
    job_result = response["output"].pop()
    response["output"].extend([
        {"type": "function_call_output", "call_id": "call-1", "output": {"jobId": "job-1", "status": "Queued"}},
        {"type": "function_call", "call_id": "call-2", "name": "GetJob", "arguments": '{"jobId":"job-1"}'},
        {**job_result, "call_id": "call-2", "output": {**json.loads(job_result["output"]), "jobId": "job-1"}},
    ])
    assert assess(response)["verdict"] == "pass"


def test_unrelated_job_result_does_not_prove_expected_tool_completed():
    response = completed_response()
    job_result = response["output"].pop()
    response["output"].extend([
        {"type": "function_call_output", "call_id": "call-1", "output": {"jobId": "job-1", "status": "Queued"}},
        {"type": "function_call", "call_id": "call-2", "name": "GetJob", "arguments": '{"jobId":"other-job"}'},
        {**job_result, "call_id": "call-2", "output": {**json.loads(job_result["output"]), "jobId": "other-job"}},
    ])
    assert assess(response)["verdict"] == "inconclusive"


def test_job_result_cannot_override_a_conflicting_job_id():
    response = completed_response()
    job_result = response["output"].pop()
    response["output"].extend([
        {"type": "function_call_output", "call_id": "call-1", "output": {"jobId": "job-1", "status": "Queued"}},
        {"type": "function_call", "call_id": "call-2", "name": "GetJob", "arguments": '{"jobId":"job-1"}'},
        {**job_result, "call_id": "call-2", "output": {**json.loads(job_result["output"]), "jobId": "different-job"}},
    ])
    assert assess(response)["verdict"] == "inconclusive"


def test_second_expected_tool_call_requires_review_of_the_one_job_pilot():
    response = completed_response()
    second = copy.deepcopy(response["output"])
    for item in second:
        item["call_id"] = "call-2"
    response["output"].extend(second)
    assert assess(response)["verdict"] == "inconclusive"


@pytest.mark.parametrize("url", [
    "http://example.workspace.discovery.azure.com",
    "https://example.com", "https://workspace.discovery.azure.com.evil.example",
    "https://user:password@example.workspace.discovery.azure.com",
    "https://example.workspace.discovery.azure.com?token=secret",
    "https://example.workspace.discovery.azure.com/other",
])
def test_credentials_cannot_be_sent_to_arbitrary_endpoint(url):
    with pytest.raises(ValueError):
        validate_endpoint(url)


def test_workspace_endpoint_is_normalized():
    url = "https://example.workspace.discovery.azure.com"
    assert validate_endpoint(url + "/") == url


def test_session_library_cannot_be_launched_as_a_live_command(tmp_path):
    result = subprocess.run([sys.executable, run_catalog_smoke.__file__, "--live"], cwd=tmp_path,
                            capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode != 0 and "Library only" in result.stderr
    assert not list(tmp_path.iterdir())


def test_upstream_workflow_can_only_plan_and_has_no_upload_or_credentials():
    root = Path(__file__).resolve().parents[2]
    raw = (root / ".github/workflows/catalog-smoke-plan.yml").read_text(encoding="utf-8")
    workflow = yaml.load(raw, Loader=yaml.BaseLoader)
    assert set(workflow["on"]) == {"workflow_dispatch"} and not workflow["on"]["workflow_dispatch"]
    assert workflow["permissions"] == {"contents": "read"}
    assert set(workflow["jobs"]) == {"plan"}
    job = workflow["jobs"]["plan"]
    assert job["runs-on"] == "ubuntu-latest" and "permissions" not in job and "environment" not in job
    actions = [s["uses"].split("@") for s in job["steps"] if "uses" in s]
    assert [a for a, _ in actions] == ["actions/checkout", "actions/setup-python"]
    assert all(len(ref) == 40 and all(ch in "0123456789abcdef" for ch in ref) for _, ref in actions)
    assert job["steps"][0]["with"]["persist-credentials"] == "false"
    assert "catalog_sweep.py --mode plan" in raw
    assert not any(x in raw for x in ("secrets.", "id-token:", "--live", "--mode live", "pull_request_target"))


def test_preflight_uses_known_agent_route_not_bare_project_metadata():
    """The observed metadata route returns 401 even while agent listing works."""
    class RouteContract:
        def _url(self, *segments, query):
            return "https://test.workspace.discovery.azure.com/" + "/".join(quote(s, safe="") for s in segments) + "?" + urlencode(query)

        async def _request(self, method, url):
            path = urlsplit(url).path
            if method == "GET" and path == "/projects/test-project/agents/CorePythonAgent":
                return 200, {"name": "CorePythonAgent", "provisioningState": "Succeeded"}
            if method == "GET" and path == "/projects/test-project":
                raise SmokeError("http_401", "Metadata route is not a valid access preflight")
            raise AssertionError(f"Unexpected request: {method} {path}")

    agent = asyncio.run(run_catalog_smoke.preflight_agent(RouteContract(), "test-project", "CorePythonAgent"))
    assert agent["name"] == "CorePythonAgent"


def test_preflight_missing_agent_is_not_reported_as_failed_sign_in():
    class MissingAgentRoute:
        def _url(self, *segments, query):
            return "/".join(segments)

        async def _request(self, method, url):
            raise SmokeError("http_404", "AgentNotFound")

    with pytest.raises(SmokeError) as caught:
        asyncio.run(run_catalog_smoke.preflight_agent(MissingAgentRoute(), "test-project", "CorePythonAgent"))
    assert caught.value.code == "agent_not_found"
    assert "Deploy" in str(caught.value)


def test_preflight_preserves_real_authorization_failure():
    class DeniedRoute:
        def _url(self, *segments, query):
            return "/".join(segments)

        async def _request(self, method, url):
            raise SmokeError("http_403", "Forbidden")

    with pytest.raises(SmokeError) as caught:
        asyncio.run(run_catalog_smoke.preflight_agent(DeniedRoute(), "test-project", "CorePythonAgent"))
    assert caught.value.code == "http_403"


OPERATION_ID = "a" * 32


def service_operation_fixture():
    """Synthetic completed-operation contract with no real resource identifiers."""
    response = completed_response()
    response["output"][0]["id"] = "fc-core"
    response["output"][1]["output"] = (
        "Tool response: Tool execution succeeded.\n"
        "Runtime details: Tool has completed successfully\n"
        f'Tool gave logs: corepython-container:\n{{"smoke_marker": "{MARKER}", "value": 42}}\n'
    )
    response["discoveryProperties"] = {"itemExtensions": {
        "fc-core": {"supercomputerOperationId": OPERATION_ID},
    }}
    operation = {
        "id": OPERATION_ID, "status": "Succeeded", "error": None,
        "result": {
            # The service's nested field is stale; its top-level lifecycle status
            # is authoritative in the repository's supercomputer poll client.
            "status": "TaskAccepted", "runtimeDetails": "Tool has completed successfully",
            "createdAt": "2020-01-02T00:00:01Z",
            "completedAt": "2020-01-02T00:00:32Z",
            "toolReport": {"logs": f'corepython-container:\n{{"smoke_marker": "{MARKER}", "value": 42}}\n'},
        },
    }
    return response, operation


def test_service_completion_and_correlated_logs_are_valid_execution_evidence():
    response, operation = service_operation_fixture()
    result = assess_response(response, expected_tool="corepython", marker=MARKER, operations={OPERATION_ID: operation})
    assert result["verdict"] == "pass"
    assert result["execution_evidence_source"] == "operation_api"
    assert result["exit_code"] == "not_exposed"
    assert result["operation_id"] == OPERATION_ID


@pytest.mark.parametrize("fault", ["pending", "failed", "error", "wrong_id", "no_link", "no_end", "bad_time", "wrong_log", "nested_failure", "missing_runtime"])
def test_service_operation_evidence_must_be_complete_and_correlated(fault):
    response, operation = service_operation_fixture()
    if fault == "pending":
        operation["status"] = "TaskAccepted"
    elif fault == "failed":
        operation["status"] = "Failed"
    elif fault == "error":
        operation["error"] = {"code": "ToolFailed"}
    elif fault == "wrong_id":
        operation["id"] = "b" * 32
    elif fault == "no_link":
        response.pop("discoveryProperties")
    elif fault == "no_end":
        operation["result"]["completedAt"] = None
    elif fault == "bad_time":
        operation["result"]["completedAt"] = "2020-01-01T00:00:00Z"
    elif fault == "wrong_log":
        operation["result"]["toolReport"]["logs"] = '{"smoke_marker":"old-run","value":42}'
    elif fault == "nested_failure":
        operation["result"]["status"] = "Failed"
    else:
        operation["result"].pop("runtimeDetails")
    assert assess_response(response, expected_tool="corepython", marker=MARKER, operations={OPERATION_ID: operation})["verdict"] != "pass"


def test_hello_world_roundtrip_does_not_require_scientific_expected_output():
    response, operation = service_operation_fixture()
    response["output"][1]["output"] = "Tool execution succeeded. Available operations: inspect, transform."
    operation["result"]["toolReport"]["logs"] = "Available operations: inspect, transform."
    result = run_catalog_smoke.assess_roundtrip(
        response, expected_tools=["corepython"], operations={OPERATION_ID: operation},
    )
    assert result["verdict"] == "pass"
    assert result["scientific_correctness_assessed"] is False


@pytest.mark.parametrize("case", ["tools_list_only", "receipt", "failed_operation"])
def test_hello_world_still_requires_an_actual_successful_tool_result(case):
    response, operation = service_operation_fixture()
    if case == "tools_list_only":
        response["output"] = [{"type": "message", "role": "assistant", "content": "I can use corepython."}]
    elif case == "receipt":
        operation["status"] = "Running"
        response["output"][1]["output"] = "Job submitted"
    else:
        operation["status"] = "Failed"
    result = run_catalog_smoke.assess_roundtrip(
        response, expected_tools=["corepython"], operations={OPERATION_ID: operation},
    )
    assert result["verdict"] != "pass"


def test_synchronous_tool_result_does_not_need_an_hpc_operation():
    response = completed_response()
    response["output"][1]["output"] = {"isError": False, "content": [{"type": "text", "text": "Tool is available"}]}
    result = run_catalog_smoke.assess_roundtrip(response, expected_tools=["corepython"], operations={})
    assert result["verdict"] == "pass"