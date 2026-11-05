#!/usr/bin/env python3
"""One explicit Discovery smoke invocation; no deployment, evaluator, or registry writes.

Default mode only writes a plan. --live permits one investigation, one conversation
and one agent response in the designated test project. A client timeout is NOT a
server-side cancellation or a dollar cap. Never retry an uncertain submission.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from smoke_contract import CONTRACT_ID, ROUNDTRIP_PROMPT


ROOT = Path(__file__).resolve().parents[2]
API_VERSION = "2026-06-01"
SCOPE = "https://discovery.azure.com/.default"
TERMINAL = {"completed", "failed", "incomplete", "cancelled", "canceled"}
OPERATION_FETCH_LIMIT = 16  # Evidence retrieval bound, NOT a remote tool-call cap.


def tool_call_observations(response):
    calls = [item.get("name") for item in response.get("output") or []
             if item.get("type") == "function_call"]
    return {"observed_tool_call_count": len(calls), "observed_calls": calls}


def pending_confirmations(response):
    """Unknown states fail closed; an already confirmed record is not a gate."""
    records = (response.get("discoveryProperties") or {}).get("confirmations") or []
    settled = {"confirmed", "approved", "rejected", "denied", "cancelled", "canceled"}
    return [record for record in records if str(record.get("status", "")).lower() not in settled]


class SmokeError(Exception):
    """Sanitized, machine-readable failure (never contains a token or HTTP body)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def validate_endpoint(value: str) -> str:
    if not re.fullmatch(r"https://[a-z0-9][a-z0-9-]*\.workspace\.discovery\.azure\.com/?", value):
        raise ValueError("Use the HTTPS workspaceApiUri returned by the Discovery workspace resource.")
    return value.rstrip("/")


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


INVESTIGATION_NAME_MAX = 24  # Service rule: 3-24 chars, letters, digits and hyphens only.


def investigation_name(agent_name: str, *, run_id: str | None = None) -> str:
    """Legacy names stay stable; labelled runs get collision-resistant sessions."""
    cleaned = re.sub(r"[^A-Za-z0-9-]+", "-", str(agent_name or "agent")).strip("-")
    if run_id is not None:
        if not re.fullmatch(r"[A-Za-z0-9-]{1,12}", run_id):
            raise ValueError("Run label must contain 1..12 letters, numbers or hyphens")
        digest = hashlib.sha256(str(agent_name).encode()).hexdigest()[:4]
        suffix = f"-{digest}-{run_id}"
        return (cleaned[:INVESTIGATION_NAME_MAX-len(suffix)].rstrip("-") or "a") + suffix
    cleaned = cleaned[:INVESTIGATION_NAME_MAX].rstrip("-")
    if len(cleaned) < 3:
        cleaned = (cleaned + "-agent").strip("-")
    return cleaned


def _objects(value, depth=0):
    if depth > 12:
        return
    if isinstance(value, str):
        try:
            yield from _objects(json.loads(value), depth + 1)
        except (ValueError, RecursionError):
            return
    elif isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _objects(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            yield from _objects(child, depth + 1)


def _job_ids(value):
    return {
        v for obj in _objects(value) for k, v in obj.items()
        if _key(k) in {"jobid", "jobname"} and isinstance(v, str) and v
    }


def _matches_tool(call, expected):
    if re.search(rf"(?:^|[^a-z0-9]){re.escape(expected.lower())}(?:$|[^a-z0-9])", str(call.get("name", "")).lower()):
        return True
    return any(
        _key(k) in {"toolid", "toolname"}
        and isinstance(v, str) and v.rstrip("/").rsplit("/", 1)[-1].lower() == expected.lower()
        for obj in _objects(call.get("arguments")) for k, v in obj.items()
    )


def _execution_result(obj, marker):
    fields = {_key(k): v for k, v in obj.items()}
    exit_code = fields.get("exitcode")
    if type(exit_code) is not int or exit_code != 0:
        return False
    if str(fields.get("status", "succeeded")).lower() not in {"succeeded", "completed", "success"}:
        return False
    stdout = fields.get("stdout", fields.get("standardoutput", ""))
    return _has_marker(stdout, marker)


def _has_marker(logs, marker):
    for line in str(logs).splitlines():
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("smoke_marker") == marker and type(data.get("value")) is int and data["value"] == 42:
            return True
    return False


def operation_ids(response, expected_tool):
    """Read only service-supplied operation links on the expected function call."""
    extensions = (response.get("discoveryProperties") or {}).get("itemExtensions") or {}
    ids = set()
    for call in response.get("output") or []:
        if call.get("type") != "function_call" or not _matches_tool(call, expected_tool):
            continue
        operation_id = (extensions.get(call.get("id")) or {}).get("supercomputerOperationId")
        if isinstance(operation_id, str) and re.fullmatch(r"[a-fA-F0-9]{32}|[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}", operation_id):
            ids.add(operation_id)
    return sorted(ids)


def linked_operations(response):
    """Include all service-supplied operation links, not just the expected tool."""
    extensions = (response.get("discoveryProperties") or {}).get("itemExtensions") or {}
    links = {}
    for item_id, extension in extensions.items():
        if not isinstance(extension, dict) or not extension.get("supercomputerOperationId"):
            continue
        operation_id = extension["supercomputerOperationId"]
        if not isinstance(operation_id, str) or not re.fullmatch(r"[a-fA-F0-9]{32}|[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}", operation_id):
            raise ValueError("Invalid service operation identifier")
        links[item_id] = operation_id
    return links


def _completed_operation(operation, operation_id, marker):
    """Use the authoritative lifecycle status, timestamps, and exact log result.

    The GA operation schema does not expose a process exit code. Its nested
    result.status may remain TaskAccepted after top-level status is Succeeded.
    See utilities/supercomputer-cli/.../poll/dataplane_api.py:get_operation_status.
    """
    if not isinstance(operation, dict) or operation.get("id") != operation_id:
        return False
    result = operation.get("result") or {}
    if (operation.get("status") != "Succeeded" or operation.get("error")
            or result.get("status") not in {"Succeeded", "TaskAccepted"}
            or result.get("runtimeDetails") != "Tool has completed successfully"):
        return False
    try:
        start = datetime.fromisoformat(result["createdAt"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(result["completedAt"].replace("Z", "+00:00"))
        if start.tzinfo is None or end.tzinfo is None or end < start:
            return False
    except (KeyError, ValueError, TypeError, AttributeError):
        return False
    return _has_marker((result.get("toolReport") or {}).get("logs"), marker)


def assess_response(response, *, expected_tool, marker, operations=None):
    """Require actual, correlated exit/stdout evidence, not agent prose or a receipt."""
    result = {"verdict": "inconclusive", "paired_tool_results": 0, "expected_tool_called": False, "reason": "Missing tool execution evidence."}
    if response.get("status") != "completed" or response.get("error"):
        return {**result, "verdict": "failed", "reason": "Agent response did not complete successfully."}
    items = response.get("output")
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        return result
    calls = [item for item in items if item.get("type") == "function_call"]
    outputs = [item for item in items if item.get("type") == "function_call_output"]
    call_ids = [item.get("call_id") for item in calls]
    output_ids = [item.get("call_id") for item in outputs]
    if (not calls or not all(isinstance(i, str) and i for i in call_ids + output_ids)
            or len(set(call_ids)) != len(call_ids) or len(set(output_ids)) != len(output_ids)
            or set(call_ids) != set(output_ids)):
        return {**result, "reason": "Missing, unmatched, or duplicate tool call_id evidence."}
    paired = {item["call_id"]: item.get("output") for item in outputs}
    expected_calls = [call for call in calls if _matches_tool(call, expected_tool)]
    result.update(paired_tool_results=len(paired), expected_tool_called=bool(expected_calls))
    if len(expected_calls) > 1:
        return {**result, "reason": "Multiple expected-tool calls need review; the one-job pilot cannot be confirmed."}
    records = [obj for value in paired.values() for obj in _objects(value)]
    for obj in records:
        fields = {_key(k): v for k, v in obj.items()}
        if (fields.get("error") or fields.get("iserror") is True
                or (type(fields.get("exitcode")) is int and fields["exitcode"] != 0)
                or str(fields.get("status", "")).lower() in {"failed", "error", "cancelled", "canceled"}):
            return {**result, "verdict": "failed", "reason": "A tool returned explicit failure evidence."}
    linked_ids = operation_ids(response, expected_tool)
    if linked_ids and operations is not None:
        if len(linked_ids) != 1:
            return {**result, "reason": "More than one tool operation needs review."}
        operation_id = linked_ids[0]
        operation = operations.get(operation_id) or {}
        if operation.get("error") or operation.get("status") in {"Failed", "Canceled"}:
            return {**result, "verdict": "failed", "reason": "The correlated tool operation failed."}
        if _completed_operation(operation, operation_id, marker):
            return {
                **result, "verdict": "pass", "operation_id": operation_id,
                "execution_evidence_source": "operation_api", "exit_code": "not_exposed",
                "reason": "Correlated operation is Succeeded with completion timestamps and the exact computed log result.",
            }
        return {**result, "reason": "Correlated operation lacks complete terminal execution evidence."}
    expected_ids = {call["call_id"] for call in expected_calls}
    jobs = set().union(*(_job_ids(paired[i]) for i in expected_ids)) if expected_ids else set()
    for call in calls:
        value = paired[call["call_id"]]
        referenced_jobs = _job_ids(call.get("arguments")) | _job_ids(value)
        related = call["call_id"] in expected_ids or bool(referenced_jobs and referenced_jobs <= jobs)
        if related and any(_execution_result(obj, marker) for obj in _objects(value)):
            return {**result, "verdict": "pass", "execution_evidence_source": "function_call_output", "exit_code": 0,
                    "reason": "Expected tool completed with exit code 0 and the run-specific computed stdout."}
    return result


def assess_roundtrip(response, *, expected_tools, operations=None):
    """Minimal smoke: one expected tool called and its successful result returned.

    No scientific ground truth, bespoke fixture, or arithmetic marker is required.
    Operation evidence covers asynchronous Discovery tools. Explicit success/error
    fields cover synchronous JSON/MCP results; unrecognized formats stay Unknown.
    """
    result = {"verdict": "inconclusive", "scientific_correctness_assessed": False,
              "reason": "No verifiable successful result from an expected tool."}
    if response.get("status") != "completed" or response.get("error"):
        return {**result, "verdict": "failed", "reason": "Agent response did not complete successfully."}
    items = response.get("output") or []
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        return result
    calls = [item for item in items if item.get("type") == "function_call"
             and any(_matches_tool(item, tool) for tool in expected_tools)]
    if len(calls) != 1:
        return {**result, "reason": "Expected one demonstration call; tool listings or unrelated context calls are not proof."}
    call = calls[0]
    paired = [item for item in items if item.get("type") == "function_call_output"
              and call.get("call_id") and item.get("call_id") == call["call_id"]]
    if len(paired) != 1 or paired[0].get("output") in (None, "", {}, []):
        return {**result, "reason": "A matching tool result was not returned."}
    output = paired[0]["output"]
    records = list(_objects(output))
    for obj in records:
        fields = {_key(k): v for k, v in obj.items()}
        if (fields.get("error") or fields.get("iserror") is True or fields.get("success") is False
                or str(fields.get("status", "")).lower() in {"failed", "error", "cancelled", "canceled"}
                or (type(fields.get("exitcode")) is int and fields["exitcode"] != 0)):
            return {**result, "verdict": "failed", "reason": "Tool result reports an execution error."}
    linked = sorted({op for tool in expected_tools for op in operation_ids(response, tool)})
    if linked:
        if len(linked) != 1:
            return {**result, "reason": "More than one compute operation needs review."}
        operation = (operations or {}).get(linked[0]) or {}
        if operation.get("id") != linked[0]:
            return {**result, "reason": "Matching compute operation evidence is missing."}
        if operation.get("error") or operation.get("status") in {"Failed", "Canceled"}:
            return {**result, "verdict": "failed", "reason": "The correlated tool operation failed."}
        if operation.get("status") != "Succeeded":
            return {**result, "reason": "Tool operation has not successfully finished; a receipt is not proof."}
        return {**result, "verdict": "pass", "operation_id": linked[0], "call_id": call["call_id"],
                "execution_evidence_source": "operation_api", "reason": "Expected tool completed successfully and returned a correlated result."}
    # A completed transport item alone is insufficient: it may contain an error
    # message or a queued-job receipt rather than a successful synchronous result.
    if not records:
        return result
    fields = {_key(k): v for k, v in records[0].items()}
    if str(fields.get("status", "")).lower() in {"queued", "pending", "running", "accepted", "taskaccepted", "notstarted"}:
        return result
    success = (fields.get("iserror") is False or fields.get("success") is True
               or str(fields.get("status", "")).lower() in {"succeeded", "success", "completed", "ok"}
               or (type(fields.get("exitcode")) is int and fields["exitcode"] == 0))
    if success and not _job_ids(output):
        return {**result, "verdict": "pass", "call_id": call["call_id"],
                "execution_evidence_source": "function_call_output", "reason": "Expected synchronous tool returned a successful result."}
    return result


def pilot_prompt(marker):
    return (
        "Run one minimal Core Python smoke test using the existing corepython tool. "
        "Use only an existing CPU node pool; no GPU, new pool, downloads, packages, "
        "external requests, or reading existing project assets. Execute exactly one "
        "Python script: import json; "
        f"print(json.dumps({{'smoke_marker': '{marker}', 'value': sum([19, 23])}})). "
        "Set a 60-second job execution timeout if supported. Read-only context and "
        "job-status calls are allowed. Wait for that job's terminal status and return "
        "its job id, exit code, stdout and stderr. Do not report success from a job "
        "submission receipt. Do not submit a second script or retry a failed job. "
        "If no existing CPU execution route is available, stop and explain the blocker."
    )


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def revision():
    completed = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10, check=False)
    return completed.stdout.strip() if completed.returncode == 0 else "unknown"


async def preflight_agent(client, project, agent_name):
    """Check the actual agent route, not the bare project metadata route.

    The workspace's GET /projects/<project> can return an empty 401 even when
    GET /projects/<project>/agents is authorized. Do not use it as a sign-in gate.
    """
    try:
        _, agent = await client._request(
            "GET", client._url("projects", project, "agents", agent_name,
                               query={"api-version": API_VERSION}),
        )
    except SmokeError as exc:
        if exc.code == "http_404":
            raise SmokeError(
                "agent_not_found",
                f"Agent {agent_name} was not found in project {project}. "
                "Deploy the approved pilot or select an existing agent; this is not a sign-in failure.",
            ) from exc
        raise
    return agent


async def run_live(args, report, output):
    import aiohttp
    from azure.identity import AzureCliCredential

    sys.path.insert(0, str(ROOT / "utilities" / "agent-evaluation" / "evaluators"))
    from azure_credential import get_credential
    from discovery_client import DiscoveryAgentClient

    # Local runs deliberately use the current CLI identity, not an implicit chain.
    credential = get_credential() if os.environ.get("GITHUB_ACTIONS") == "true" else AzureCliCredential(process_timeout=30)
    try:
        token = await asyncio.to_thread(credential.get_token, SCOPE)
    except Exception as exc:
        credential.close()
        raise SmokeError("authentication", "Discovery token acquisition failed; refresh the authorized sign-in. No deployment attempted.") from exc
    try:
        await _run_live_session(args, report, output, credential, token, aiohttp, DiscoveryAgentClient)
    finally:
        credential.close()


async def _run_live_session(args, report, output, credential, token, aiohttp, DiscoveryAgentClient):
    current = {"token": token}

    class SmokeClient(DiscoveryAgentClient):
        async def _request(self, method, url, body=None):
            # CLI tokens can be returned near expiry; long polls must not reuse a stale one.
            if current["token"].expires_on < time.time() + 300:
                current["token"] = await asyncio.to_thread(credential.get_token, SCOPE)
                self.token = current["token"].token
            if urlsplit(url).netloc != urlsplit(self.base).netloc or urlsplit(url).scheme != "https":
                raise SmokeError("endpoint", "Refused request outside the selected workspace.")
            session = await self._ensure_session()
            request_started = time.monotonic()
            request_started_at = datetime.now(timezone.utc).isoformat()
            async with session.request(
                method, url, json=body, allow_redirects=False,
                headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json"},
                timeout=aiohttp.ClientTimeout(total=45),
            ) as response:
                report.setdefault("http_events", []).append({
                    "phase": report["phase"], "method": method,
                    "path": urlsplit(url).path, "status": response.status,
                    "started_at": request_started_at,
                    "response_headers_ms": round((time.monotonic() - request_started) * 1000, 3),
                    "request_id": response.headers.get("x-ms-request-id"),
                    "correlation_id": response.headers.get("x-ms-correlation-request-id"),
                })
                if response.status >= 300:
                    raise SmokeError(f"http_{response.status}", f"HTTP {response.status} during {report['phase']}; stopped without retry.")
                raw = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    raw.extend(chunk)
                    if len(raw) > 4 * 1024 * 1024:
                        raise SmokeError("response_size", "Response exceeds the 4 MiB capture limit.")
                payload = json.loads(raw) if raw else {}
                if not isinstance(payload, dict):
                    raise SmokeError("response_shape", "Expected a JSON object from Discovery.")
                return response.status, payload

        async def create_response(self, conversation, agent, query, *, label=""):
            """Same request as the base client, plus input files attached the way Studio's attach button does."""
            body = {"input": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": query}]}],
                    "agent": {"type": "agent_reference", "name": agent}}
            if report.get("storage_assets"):
                body["discoveryProperties"] = {"storageAssets": list(report["storage_assets"])}
            _, payload = await self._request("POST", self._url("conversations", conversation, "openai", "v1", "responses"), body)
            if not payload.get("id"):
                raise SmokeError("response_shape", "Response create returned no id.")
            return payload["id"]

    async with SmokeClient(args.workspace_api_url, token=token.token, api_version=API_VERSION) as client:
        report["phase"] = "agent_preflight"
        agent = await preflight_agent(client, args.project, args.agent)
        report["agent_definition_sha256"] = hashlib.sha256(json.dumps(agent, sort_keys=True).encode()).hexdigest()
        report["agent_provisioning_state"] = agent.get("provisioningState", (agent.get("properties") or {}).get("provisioningState", "unknown"))
        if str(report["agent_provisioning_state"]).lower() != "succeeded":
            raise SmokeError("agent_not_ready", "The existing agent is not in Succeeded state; no invocation attempted.")
        report["load_status"] = "passed"
        if args.preflight_only:
            report.update(verdict="ready", reason="Agent read succeeded; no investigation or invocation attempted.", phase="preflight_complete")
            return
        report["phase"] = "create_investigation"
        await client.create_investigation(args.project, report["investigation"], description="One manual catalog smoke test; no deployments or registry changes.")
        report["phase"] = "create_conversation"
        report["conversation_id"] = await client.create_conversation(args.project, report["investigation"])
        # Persist identifiers before the potentially billable POST. No retries.
        report["phase"] = "submit_response"
        report["submission_attempted"] = True
        write_json(output / "summary.json", report)
        report["response_id"] = await client.create_response(report["conversation_id"], args.agent, report["prompt"])
        report["phase"] = "poll_response"
        write_json(output / "summary.json", report)
        while True:
            try:
                _, response = await client._request("GET", client._url("conversations", report["conversation_id"], "openai", "v1", "responses", report["response_id"]))
            except asyncio.TimeoutError:
                # A slow read-only status poll is not a job failure; the caller's deadline bounds this.
                report["poll_timeouts"] = report.get("poll_timeouts", 0) + 1
                await asyncio.sleep(3)
                continue
            if response.get("id") != report["response_id"]:
                raise SmokeError("response_id", "Returned response does not match the submitted response id.")
            # Raw responses can include private data; only write to local/restricted storage.
            write_json(output / "response.json", response)
            report.update(tool_call_observations(response))
            waiting = pending_confirmations(response)
            if waiting:
                # Persist actionable status immediately, without exposing tool
                # arguments or approving anything on the user's behalf.
                notice = {"status": "awaiting_human_approval", "agent": args.agent,
                          "project": args.project, "investigation": report["investigation"],
                          "conversation_id": report["conversation_id"], "response_id": report["response_id"],
                          "tools": sorted({str(r.get("toolName", "unknown")) for r in waiting}),
                          "action": "Review the tool request in Studio and Proceed or reject manually"}
                write_json(output / "approval-needed.json", notice)
                if not report.get("human_approval_pending"):
                    print(f"HUMAN APPROVAL REQUIRED: {args.agent} in {args.project}; review its Studio session. No automatic approval.", flush=True)
                report.update(human_approval_pending=True, phase="approval_checkpoint",
                              verdict="inconclusive", tool_roundtrip_status="inconclusive",
                              reason="confirmation_requires_human_review; reservation retained; no automatic approval",
                              remote_may_be_running=True, server_may_be_running=True,
                              response_status=response.get("status"), usage=response.get("usage", "unknown"))
                # Return the worker promptly. The queue retains its reservation;
                # only subsequent read-only reconciliation can release it.
                write_json(output / "operations.json", {})
                write_json(output / "summary.json", report)
                return
            elif report.get("human_approval_pending"):
                report["human_approval_pending"] = False
                (output / "approval-needed.json").unlink(missing_ok=True)
                write_json(output / "summary.json", report)
            if response.get("status") in TERMINAL:
                report["server_terminal_observed"] = True
                report["response_status"] = response.get("status")
                report["usage"] = response.get("usage", "unknown")
                report["phase"] = "tool_operation_evidence"
                operations = {}
                linked_ids = sorted(set(linked_operations(response).values()))
                # Same bound as read-only reconciliation; the classifier judges each operation.
                if len(linked_ids) > OPERATION_FETCH_LIMIT:
                    raise SmokeError("multiple_operations", "More than sixteen tool operations; review required.")
                for operation_id in linked_ids:
                    _, operations[operation_id] = await client._request(
                        "GET", client._url("tools", "projects", args.project, "operations", operation_id,
                                           query={"api-version": API_VERSION}),
                    )
                write_json(output / "operations.json", operations)
                if args.contract == "corepython-pilot":
                    assessment = assess_response(response, expected_tool="corepython", marker=report["marker"], operations=operations)
                else:
                    assessment = assess_roundtrip(response, expected_tools=report["expected_tools"], operations=operations)
                report.update(assessment)
                report["tool_roundtrip_status"] = assessment["verdict"]
                report["phase"] = "complete"
                return
            await asyncio.sleep(3)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-api-url", required=True)
    parser.add_argument("--project", default="weekly-agent-svc-test", choices=["weekly-agent-svc-test"])
    parser.add_argument("--agent", default="CorePythonAgent")
    parser.add_argument("--contract", choices=["roundtrip", "corepython-pilot"], default="roundtrip")
    parser.add_argument("--expected-tool", action="append", help="Declared tool name to recognize (repeat for agents with several tools; only one must be called).")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=300)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--live", action="store_true", help="Allow ONE real, potentially billable agent invocation.")
    mode.add_argument("--preflight-only", action="store_true", help="Read the existing agent only; no cloud writes or invocation.")
    args = parser.parse_args(argv)
    try:
        args.workspace_api_url = validate_endpoint(args.workspace_api_url)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", args.agent):
            raise ValueError("Invalid agent name.")
        if not 30 <= args.timeout <= 600:
            raise ValueError("Timeout must be between 30 and 600 seconds.")
        if args.agent != "CorePythonAgent" and not args.expected_tool and args.live:
            raise ValueError("Supply the agent's declared --expected-tool; do not count unrelated platform helpers.")
        if args.contract == "corepython-pilot" and args.agent != "CorePythonAgent":
            raise ValueError("The legacy arithmetic pilot applies only to CorePythonAgent.")
    except ValueError as exc:
        parser.error(str(exc))
    run_id = uuid.uuid4().hex[:16]
    output = args.output_dir.resolve() / run_id
    output.mkdir(parents=True, exist_ok=False)
    report = {
        "schema_version": 1, "run_id": run_id, "started_at": datetime.now(timezone.utc).isoformat(),
        "mode": "live" if args.live else "preflight" if args.preflight_only else "plan", "verdict": "not_run", "phase": "plan",
        "workspace_api_url": args.workspace_api_url, "project": args.project, "agent": args.agent,
        "contract": CONTRACT_ID if args.contract == "roundtrip" else "corepython-arithmetic-pilot-v1",
        "expected_tools": args.expected_tool or ["corepython"],
        "load_status": "not_run", "tool_roundtrip_status": "not_run",
        "catalog_revision": revision(), "api_version": API_VERSION,
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "investigation": investigation_name(args.agent), "marker": f"{args.agent}-{run_id}",
        "max_agent_invocations": 1, "concurrency": 1, "automatic_retries": 0,
        "client_timeout_seconds": args.timeout, "submission_attempted": False,
        "server_terminal_observed": False, "server_cancellation": "not_attempted",
        "cost": "unknown", "image_digest": "not_resolved",
        "scope": "One agent/prompt/tool-roundtrip; not scientific-correctness or starter-kit coverage.",
    }
    report["prompt"] = ROUNDTRIP_PROMPT if args.contract == "roundtrip" else pilot_prompt(report["marker"])
    write_json(output / "summary.json", report)
    started = time.monotonic()
    if args.live or args.preflight_only:
        async def bounded_run():
            await asyncio.wait_for(run_live(args, report, output), timeout=args.timeout)
        try:
            asyncio.run(bounded_run())
        except SmokeError as exc:
            report.update(verdict="blocked", error={"code": exc.code, "message": str(exc)})
        except TimeoutError:
            report.update(verdict="timeout", error={"code": "client_timeout", "message": "Stopped waiting. A submitted server job may still be running; do not resubmit automatically."})
        except (Exception, SystemExit) as exc:
            report.update(verdict="blocked", error={"code": type(exc).__name__, "message": "Runner failed; no retry. Inspect the environment and saved run identifiers."})
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    report["server_may_be_running"] = report["submission_attempted"] and (
        not report["server_terminal_observed"] or report["verdict"] != "pass"
    )
    write_json(output / "summary.json", report)
    summary = (
        f"# Catalog smoke: {report['verdict']}\n\n"
        f"- Run: {run_id}\n- Agent: {args.agent}\n- Project: {args.project}\n"
        f"- Phase: {report['phase']}\n- Submission attempted: {report['submission_attempted']}\n"
        f"- Server may still be running: {report['server_may_be_running']}\n"
        f"- Result: {report.get('reason', report.get('error', {}).get('message', 'Plan only; no network calls.'))}\n\n"
        "No deployments, registry writes, permission changes, or automatic retries.\n"
        "A client timeout does not cancel a server-side job or cap cost.\n"
    )
    (output / "summary.md").write_text(summary, encoding="utf-8")
    # Correlation/KQL generation is local only; raw Azure log fetching is opt-in.
    from smoke_diagnostics import write_diagnostics
    write_diagnostics(output)
    print(summary)
    print(f"Evidence: {output}")
    return 0 if report["verdict"] in {"pass", "not_run", "ready"} else 1


if __name__ == "__main__":
    raise SystemExit(main())