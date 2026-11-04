#!/usr/bin/env python3
"""One catalog queue: offline plan, read-only readiness, or explicitly approved live sweep.

No image builds/copies, shared tool edits, role grants, automatic retries, or agent
overwrites. Native connectors without a verified deployment adapter remain explicit
blocked rows. A client timeout is not remote cancellation or a monetary cap.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import os
import re
import sys
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote, urlsplit

import yaml

from generate_smoke_manifest import generate_manifest
from plan_catalog_smoke import LANE_CONCURRENCY, LANES, build_plan
from run_catalog_smoke import (
    API_VERSION, SCOPE, TERMINAL, SmokeError, _matches_tool, assess_roundtrip,
    investigation_name, revision, run_live, validate_endpoint,
    pending_confirmations,
)
from smoke_contract import CONTRACT_ID
from smoke_diagnostics import write_diagnostics

# Agent says it stopped because its input was absent (correct behavior; a harness staging gap).
MISSING_INPUT = re.compile(
    r"(blocker|blocked|cannot (?:run|proceed)|could not (?:run|find)|can.t (?:run|find))[\s\S]{0,400}?"
    r"(not (?:available|present|found)|no (?:input|valid|workspace|project|discovery://|pdb|verilog|csv)|missing|upload)",
    re.IGNORECASE)


def save_json(path, data):
    """Atomic checkpoints; a crash must not turn a started case into a new case."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    # Windows readers/indexers may briefly hold the destination without delete
    # sharing. Preserve atomic publication and bound file-lock contention.
    for attempt in range(6):
        try:
            os.replace(temporary, path)
            break
        except PermissionError:
            if attempt == 5:
                raise
            time.sleep(0.1 * (attempt + 1))


class SweepStore:
    def __init__(self, root, plan_hash):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        marker = root / "plan-hash"
        try:
            with marker.open("x", encoding="utf-8") as stream:
                stream.write(plan_hash)
        except FileExistsError:
            if marker.read_text(encoding="utf-8") != plan_hash:
                raise ValueError("This output directory belongs to a different plan; choose a new directory.")

    def _folder(self, name):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", name):
            raise ValueError("Invalid catalog case name")
        return self.root / name

    def claim(self, name):
        try:
            self._folder(name).mkdir()
        except FileExistsError:
            return False
        self.save(name, {"status": "started", "reason": "Do not resubmit if interrupted."})
        return True

    def save(self, name, data):
        save_json(self._folder(name) / "case.json", data)

    def read(self, name):
        path = self._folder(name) / "case.json"
        if not path.exists():
            return {"status": "uncertain", "reason": "An existing case directory has no final checkpoint."}
        data = json.loads(path.read_text(encoding="utf-8"))
        summary_path = self._folder(name) / "summary.json"
        if summary_path.exists():
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            # Submission IDs are persisted before final case counters. A stopped
            # coordinator must not lose already-consumed invocation allowance.
            data["response_submissions"] = max(
                data.get("response_submissions", 0),
                int(bool(summary.get("submission_attempted") or summary.get("response_id"))),
            )
        if data.get("status") == "started":
            data = {**data, "status": "uncertain", "reason": "Previous execution was interrupted; inspect saved IDs, do not resubmit."}
        return data


async def run_queue(cases, worker, *, concurrency=2, continue_independent=False, held_cases=None):
    if not 1 <= concurrency <= 4:
        raise ValueError("Global concurrency must be 1..4")
    pending = list(cases)
    held = list(held_cases or [])
    active = {}
    results = {}
    stopped = False
    while pending or active:
        occupied = list(active.values()) + held
        while not stopped and pending and len(occupied) < concurrency:
            counts = Counter(case["lane"] for case in occupied)
            ready_index = next((i for i, case in enumerate(pending)
                                if counts[case["lane"]] < LANE_CONCURRENCY[case["lane"]]), None)
            if ready_index is None:
                break
            case = pending.pop(ready_index)
            active[asyncio.create_task(worker(case))] = case
            occupied.append(case)
        if not active:
            holders = ", ".join(case["catalog_agent"] for case in held)
            for case in pending:
                results[case["catalog_agent"]] = {
                    "catalog_agent": case["catalog_agent"], "status": "not_run",
                    "reason": ("Stopped after denied access or unresolved work" if stopped else
                               f"Capacity reserved for unresolved existing cases: {holders}"),
                }
            break
        finished, _ = await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
        for task in finished:
            case = active.pop(task)
            result = task.result()
            results[case["catalog_agent"]] = {"catalog_agent": case["catalog_agent"], **result}
            if result.get("stop_new_work"):
                stopped = True
            if result.get("status") == "uncertain":
                if continue_independent:
                    # The unresolved agent retains both global and lane capacity.
                    held.append(case)
                else:
                    stopped = True
    return [results[case["catalog_agent"]] for case in cases]


SYNC_PLATFORM_HELPERS = {
    "GetResourceContext", "GetNodePoolContext", "PreviewResource",
    "WriteResource", "ShareResource", "UpdateResourceDetails",
}


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


def _nodepool_rejected_before_submission(call, response, logs):
    """Recognize only the observed, correlated service argument-validation error."""
    correlation = (response.get("discoveryProperties") or {}).get("correlationId")
    if not correlation or not isinstance(logs, dict):
        return False
    try:
        arguments = json.loads(call.get("arguments", "{}"))
    except (TypeError, ValueError):
        return False
    if not isinstance(arguments, dict) or arguments.get("nodePool") not in (None, ""):
        return False
    outputs = [item for item in response.get("output", []) if item.get("type") == "function_call_output"
               and item.get("call_id") == call.get("call_id")]
    if len(outputs) != 1 or outputs[0].get("status") != "completed" or not str(outputs[0].get("output", "")).startswith("Error:"):
        return False
    for table in logs.get("tables", []):
        columns = [column["name"] for column in table.get("columns", [])]
        for values in table.get("rows", []):
            row = dict(zip(columns, values))
            exception = str(row.get("Exception", ""))
            if (row.get("CorrelationId") == correlation and row.get("LogLevel") == "Error"
                    and exception.startswith("System.ArgumentException: The 'nodePool' parameter is required.")):
                return True
    return False


def classify_execution(report, response, operations, *, validation_logs=None):
    """Separate failed coverage from possible in-flight work before queue resume.

    A completed response containing only completed synchronous platform helpers
    cannot pass the catalog roundtrip, but is not an unresolved compute job.
    Unknown calls/receipts, active confirmations and missing operations still stop.
    """
    uncertain = {"status": "uncertain", "remote_may_be_running": True,
                 "tool_roundtrip": "inconclusive", "reason": "remote_work_not_resolved"}
    if response.get("id") != report.get("response_id"):
        return {**uncertain, "reason": "response_id_not_verified"}
    if pending_confirmations(response):
        return {**uncertain, "reason": "confirmation_requires_human_review"}
    if response.get("status") not in TERMINAL:
        return uncertain
    links = linked_operations(response)
    for operation_id in set(links.values()):
        operation = operations.get(operation_id) or {}
        if operation.get("id") != operation_id or operation.get("status") not in {"Succeeded", "Failed", "Canceled"}:
            return uncertain
    items = response.get("output") or []
    calls = [item for item in items if item.get("type") == "function_call"]
    assessment = assess_roundtrip(response, expected_tools=report["expected_tools"], operations=operations)
    expected = [call for call in calls if any(_matches_tool(call, tool) for tool in report["expected_tools"])]
    validation_rejected = (not links and len(expected) == 1
                           and _nodepool_rejected_before_submission(expected[0], response, validation_logs))
    rejected_calls = []
    for call in calls:
        if call.get("id") in links:
            continue  # Its actual remote lifecycle has been verified above.
        paired = [item for item in items if item.get("type") == "function_call_output"
                  and call.get("call_id") and item.get("call_id") == call["call_id"]]
        helper_complete = (call.get("name") in SYNC_PLATFORM_HELPERS
                           and len(paired) == 1 and paired[0].get("status") == "completed"
                           and call.get("status") == "completed")
        verified_sync_result = call in expected and assessment["verdict"] == "pass"
        # Observed explicit argument-validation rejections (no job exists). A generic
        # "unexpected error" stays uncertain: the job may have been submitted.
        sync_rejection = (call in expected and call.get("status") == "completed" and len(paired) == 1
                          and paired[0].get("status") == "completed"
                          and SYNC_REJECTION.match(str(paired[0].get("output", ""))))
        if sync_rejection:
            rejected_calls.append(call)
        elif not helper_complete and not verified_sync_result and not (call in expected and validation_rejected):
            return uncertain
    if not expected:
        message = " ".join(c.get("text") or "" for item in items if item.get("type") == "message"
                           for c in item.get("content") or [])
        reason = "own_tool_not_called"
        if MISSING_INPUT.search(message):
            reason = "input_missing_reported_by_agent (own_tool_not_called)"
        return {"status": "failed", "tool_roundtrip": "failed", "remote_may_be_running": False,
                "reason": reason, "observed_calls": [call.get("name") for call in calls]}
    if validation_rejected:
        return {"status": "failed", "tool_roundtrip": "failed", "remote_may_be_running": False,
                "reason": "tool_arguments_invalid: missing nodePool (correlated backend validation error)",
                "failure_stage": "tool_argument_validation"}
    if assessment["verdict"] != "pass":
        linked_expected = [call for call in expected if call.get("id") in links]
        if rejected_calls and not linked_expected:
            first = next(item for item in items if item.get("type") == "function_call_output"
                         and item.get("call_id") == rejected_calls[0].get("call_id"))
            return {"status": "failed", "tool_roundtrip": "failed", "remote_may_be_running": False,
                    "reason": "tool_rejected_arguments: " + str(first.get("output", ""))[:160],
                    "failure_stage": "tool_argument_validation"}
        states = [(operations.get(links[call["id"]]) or {}).get("status") for call in linked_expected]
        if "Succeeded" in states and len(expected) > 1:
            # The agent's own tool produced a verified result, but only after extra attempts.
            return {"status": "passed", "tool_roundtrip": "pass", "remote_may_be_running": False,
                    "agent_retried": True,
                    "reason": (f"own tool operation Succeeded after agent retries "
                               f"({states.count('Succeeded')} of {len(expected)} own-tool calls succeeded)")}
        if linked_expected and all(state in {"Failed", "Canceled"} for state in states):
            return {"status": "failed", "tool_roundtrip": "failed", "remote_may_be_running": False,
                    "reason": f"own_tool_operations_failed ({len(linked_expected)} of {len(linked_expected)})"}
    return {"status": "passed" if assessment["verdict"] == "pass" else assessment["verdict"],
            "tool_roundtrip": assessment["verdict"], "reason": assessment["reason"],
            "remote_may_be_running": False, "assessment": assessment}


def tool_anchored_prompt(base_prompt, expected_tools, lane, input_files=()):
    tools = ", ".join(expected_tools)
    prompt = (
        f"REQUIRED CATALOG TOOL: {tools}. Use exactly one action from that tool. "
        "GetNodePoolContext, GetResourceContext, WriteResource, PreviewResource, "
        "ShareResource and UpdateResourceDetails do NOT satisfy the demonstration; "
        "they are helpers only. If a nodePool is needed, call GetNodePoolContext first and "
        "pass the full nonempty nodePool resource ID from a suitable existing pool. Then run the "
        "smallest valid domain task with the provided input. Do not list tools, create "
        "placeholder sample files in place of the task, or check versions. If the task itself "
        "says to write its input from given values, writing that input is part of the task. "
        "This one task is pre-approved: run it now instead of stopping at a plan. "
        "Return the actual result and any output path. "
        "If required input or capacity is missing, report the blocker and stop. Do not retry.\n\n" + base_prompt
    )
    if input_files:
        prompt += ("\nThe input files are attached to this message as storage assets: "
                   + ", ".join(name.rsplit("/", 1)[-1] for name in input_files)
                   + ". Use them as the tool input; do not ask for an upload. "
                   "For tools exposing inputData, mount the attached asset there and pass "
                   "a container-local directory to the action's input/path argument, not a "
                   "discovery:// URI as a filesystem path. Use only the named files for "
                   "this task; a shared folder may contain other agents' fixtures. If the "
                   "action scans a directory, copy only the named files to a writable "
                   "task-specific directory. Never modify the read-only asset mount.")
    if lane == "cpu":
        prompt += "\nUse only an existing CPU node pool, never a GPU pool."
    return prompt


ASSET_ID = re.compile(r"^/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft\.Discovery/"
                      r"storageContainers/[^/]+/storageAssets/[^/]+$")


def load_input_assets(path):
    """Map input file names to Discovery storage asset ARM IDs (environment-specific, not in the catalog)."""
    if not path:
        return {}
    assets = (yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}).get("assets") or {}
    if not isinstance(assets, dict):
        raise ValueError("input assets file needs an 'assets' mapping")
    for name, asset_id in assets.items():
        if not isinstance(asset_id, str) or not ASSET_ID.match(asset_id):
            raise ValueError(f"Not a Discovery storage asset ARM ID for {name}")
    return assets


def staged_inputs(case, assets):
    """Return (asset IDs, unstaged file names) for the case's declared input files."""
    files = case.get("input_files") or []
    missing = [name for name in files if name not in assets]
    # Several files can live in one folder asset; attach each asset once (duplicate mounts are rejected).
    ids = list(dict.fromkeys(assets[name] for name in files if name in assets))
    return ids, missing


def build_agent_payload(source, model, bindings):
    """Preserve catalog instructions and Discovery fields. Never drop native tools."""
    if source.get("kind") != "prompt":
        raise ValueError("Unsupported agent kind")
    if source.get("tools"):
        raise ValueError("A verified native/connector deployment adapter is required; tools will not be silently omitted.")
    document = copy.deepcopy(source)
    extensions = document.get("discoveryExtensions") or {}
    for tool in extensions.get("tools") or []:
        original = tool.get("toolId")
        if original not in bindings:
            raise ValueError("Unresolved tool binding")
        tool["toolId"] = bindings[original]
    definition = {"kind": "prompt", "model": model, "instructions": document.get("instructions", "")}
    options = (document.get("model") or {}).get("options") or {}
    names = {"temperature": "temperature", "topP": "top_p", "maxOutputTokens": "max_output_tokens", "allowMultipleToolCalls": "parallel_tool_calls"}
    overlays = {"model": model, "tool_bindings": bindings, "omitted_model_options": {}}
    for key, value in options.items():
        if key not in names:
            raise ValueError(f"Unsupported model option: {key}")
        # The verified gpt-5-mini pilot omitted unsupported temperature/top-p.
        # This explicit model-compatibility overlay is saved, never hidden.
        if model == "gpt-5-mini" and key in {"temperature", "topP"}:
            overlays["omitted_model_options"][key] = value
        else:
            definition[names[key]] = value
    payload = {"name": document["name"], "foundryDetails": {
        "description": document.get("description", ""), "definition": definition,
    }}
    for key in ("humanInTheLoop", "tools", "knowledgeBases"):
        if key in extensions:
            payload[key] = extensions.pop(key)
    if extensions:
        payload["discoveryExtensions"] = extensions
    # Discovery expands runtime instruction tokens such as {{nodePoolContext}}.
    # These are not missing deployment connections and must remain untouched.
    config_only = {key: value for key, value in payload.items() if key != "foundryDetails"}
    config_only["model"] = definition["model"]
    if re.search(r"\{\{[^}]+\}\}", json.dumps(config_only)):
        raise ValueError("Unresolved connection or resource placeholder")
    return payload, overlays


def _normal(value):
    return re.sub(r"[^a-z0-9]", "", value.lower())


def resolve_bindings(source, folder, resources):
    local = [(path.parent.name, yaml.safe_load(path.read_text(encoding="utf-8")))
             for path in sorted((folder / "tools").glob("*/tool.yaml"))]
    bindings = {}
    expected = []
    for reference in (source.get("discoveryExtensions") or {}).get("tools") or []:
        original = reference["toolId"]
        hint = _normal(original.rsplit("/", 1)[-1].strip("{}"))
        hint = re.sub(r"(toolid|id)$", "", hint)
        matches = [(slug, tool) for slug, tool in local if hint in {_normal(slug), _normal(tool.get("name", slug))}]
        if len(local) == 1:
            matches = local
        if len(matches) != 1:
            raise ValueError("Cannot unambiguously bind catalog tool reference")
        slug, tool = matches[0]
        aliases = {_normal(slug), _normal(tool.get("name", slug))}
        # A deliberately renamed, isolated tool definition must bind its exact
        # registered name, not also the shared tool matching the folder slug.
        declared = _normal(tool.get("name", slug))
        found = [resource for resource in resources if _normal(resource["name"].rsplit("/", 1)[-1]) == declared]
        if not found:
            found = [resource for resource in resources if _normal(resource["name"].rsplit("/", 1)[-1]) in aliases]
        if len(found) != 1:
            raise ValueError("Registered tool is missing or ambiguous; no tool/image will be created")
        bindings[original] = found[0]["id"]
        expected.append(found[0]["name"].rsplit("/", 1)[-1])
    return bindings, expected


SYNC_REJECTION = re.compile(r"Error: (Invalid nodePool resource type|Resource not found|Duplicate mount path detected"
                            r"|Invalid nodePool value: '[^']+'\. Expected an ARM resource ID in format:"
                            r"|A file named '[^']+' already exists in this conversation\. Use a different file name\."
                            r"|The '[^']+' function call is missing required parameters)")
SERVICE_UNECHOED_OPTIONS = {"max_output_tokens", "parallel_tool_calls"}


def matches_deployed(payload, agent):
    foundry = agent.get("foundryDetails") or {}
    definition = ((foundry.get("versions") or {}).get("latest") or {}).get("definition") or foundry.get("definition") or {}
    expected = payload["foundryDetails"]["definition"]
    # Observed: the service accepts but does not echo these model options. A missing
    # echo is not a different definition; any other value still is.
    if any(definition.get(key) != value for key, value in expected.items()
           if not (key in SERVICE_UNECHOED_OPTIONS and definition.get(key) is None)):
        return False
    for key in ("tools", "humanInTheLoop", "knowledgeBases"):
        if key in payload and agent.get(key) != payload[key]:
            return False
    return all((agent.get("discoveryExtensions") or {}).get(key) == value
               for key, value in payload.get("discoveryExtensions", {}).items())


class CatalogHttp:
    """Shared read/deploy session; same two approved hosts, no retries/redirects."""
    def __init__(self, endpoint):
        self.endpoint = endpoint
        self.tokens = {}

    async def __aenter__(self):
        import aiohttp
        from azure.identity import AzureCliCredential
        if os.environ.get("GITHUB_ACTIONS") == "true":
            sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "utilities/agent-evaluation/evaluators"))
            from azure_credential import get_credential
            self.credential = get_credential()
        else:
            self.credential = AzureCliCredential(process_timeout=30)
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=45))
        return self

    async def __aexit__(self, *args):
        await self.session.close()
        self.credential.close()

    async def request(self, method, url, body=None):
        host = urlsplit(url).netloc
        if urlsplit(url).scheme != "https" or host not in {urlsplit(self.endpoint).netloc, "management.azure.com"}:
            raise ValueError("Refused request outside approved hosts")
        scope = "https://management.azure.com/.default" if host == "management.azure.com" else SCOPE
        token = self.tokens.get(scope)
        if token is None or token.expires_on < time.time() + 60:
            token = await asyncio.to_thread(self.credential.get_token, scope)
            self.tokens[scope] = token
        async with self.session.request(method, url, json=body, allow_redirects=False,
                                        headers={"Authorization": f"Bearer {token.token}"}) as response:
            if response.status >= 300:
                raise SmokeError(f"http_{response.status}", f"HTTP {response.status}; no retry")
            raw = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                raw.extend(chunk)
                if len(raw) > 8 * 1024 * 1024:
                    raise ValueError("Response too large")
            return json.loads(raw) if raw else {}

    async def collection(self, url):
        values = []
        seen = set()
        while url:
            if url in seen or len(seen) >= 20:
                raise ValueError("Invalid or excessive pagination")
            seen.add(url)
            page = await self.request("GET", url)
            values.extend(page.get("value") or [])
            url = page.get("nextLink")
        return values


async def reconcile_sweep(args, plan, store):
    """Read-only remote reconciliation; never submit, cancel, deploy, or retry."""
    results = []
    async with CatalogHttp(args.workspace_api_url) as client:
        for case in plan["cases"]:
            name = case["catalog_agent"]
            folder = store._folder(name)
            if not folder.exists():
                results.append({"catalog_agent": name, "status": "not_run"})
                continue
            previous = store.read(name)
            if previous.get("status") != "uncertain":
                results.append({"catalog_agent": name, **previous})
                continue
            summary_path = folder / "summary.json"
            if not summary_path.exists():
                results.append({"catalog_agent": name, **previous})
                continue
            report = json.loads(summary_path.read_text(encoding="utf-8"))
            if (report.get("workspace_api_url") != args.workspace_api_url or report.get("project") != args.project
                    or not report.get("conversation_id") or not report.get("response_id")):
                results.append({"catalog_agent": name, **previous})
                continue
            stamp = uuid.uuid4().hex[:12]
            evidence = folder / "reconciliation" / stamp
            response_url = (f"{args.workspace_api_url}/conversations/{quote(report['conversation_id'], safe='')}"
                            f"/openai/v1/responses/{quote(report['response_id'], safe='')}")
            response = await client.request("GET", response_url)
            save_json(evidence / "response.json", response)
            ids = set(linked_operations(response).values())
            if len(ids) > 16:
                results.append({"catalog_agent": name, **previous, "reason": "Excessive operation links require review"})
                continue
            operations = {}
            for operation_id in sorted(ids):
                url = (f"{args.workspace_api_url}/tools/projects/{quote(args.project, safe='')}/operations/"
                       f"{operation_id}?api-version={API_VERSION}")
                operations[operation_id] = await client.request("GET", url)
            save_json(evidence / "operations.json", operations)
            validation_logs = None
            validation_path = folder / "error-detail-evidence" / "error-detail.json"
            validation_manifest = validation_path.parent / "manifest.json"
            if validation_path.is_file() and validation_manifest.is_file():
                records = json.loads(validation_manifest.read_text(encoding="utf-8"))
                digest = hashlib.sha256(validation_path.read_bytes()).hexdigest()
                if any(record.get("name") == "error-detail" and record.get("http_status") == 200
                       and record.get("body_sha256") == digest for record in records):
                    validation_logs = json.loads(validation_path.read_text(encoding="utf-8"))
            resolved = classify_execution(report, response, operations, validation_logs=validation_logs)
            save_json(evidence / "decision.json", {
                "previous_case": previous, "decision": resolved,
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "response_sha256": hashlib.sha256((evidence / "response.json").read_bytes()).hexdigest(),
                "validation_log_sha256": hashlib.sha256(validation_path.read_bytes()).hexdigest() if validation_logs is not None else None,
                "new_submissions": 0,
            })
            updated = {**previous, **resolved, "reconciliation": str(evidence.relative_to(args.output_dir)),
                       "original_reason": previous.get("reason"), "usage": response.get("usage", previous.get("usage"))}
            store.save(name, updated)
            results.append({"catalog_agent": name, **updated})
            print(f"{name}: reconciled {resolved['status']}; new submissions=0", flush=True)
    return results


async def execute_sweep(args, plan, store):
    prior = {case["catalog_agent"]: store.read(case["catalog_agent"])
             for case in plan["cases"] if store._folder(case["catalog_agent"]).exists()}
    unresolved = [case for case in plan["cases"] if prior.get(case["catalog_agent"], {}).get("status") == "uncertain"]
    if args.mode == "live" and unresolved and not args.continue_independent:
        return [{"catalog_agent": case["catalog_agent"], **prior.get(case["catalog_agent"], {
            "status": "not_run", "reason": "Existing uncertain work must be reconciled before new submissions.",
        })} for case in plan["cases"]]
    arm = f"https://management.azure.com/subscriptions/{args.subscription}/resourceGroups/{args.resource_group}/providers/Microsoft.Discovery"
    project_path = f"{args.workspace_api_url}/projects/{quote(args.project, safe='')}"
    async with CatalogHttp(args.workspace_api_url) as client:
        resources = await client.collection(f"{arm}/tools?api-version={API_VERSION}")
        deployed = {agent["name"]: agent for agent in await client.collection(f"{project_path}/agents?api-version={API_VERSION}")}
        if args.mode == "live":
            await client.request("GET", f"{arm}/workspaces/{urlsplit(args.workspace_api_url).hostname.split('.')[0]}/chatModelDeployments/{quote(args.model, safe='')}?api-version={API_VERSION}")
        remaining = args.max_invocations - sum(row.get("response_submissions", 0) for row in prior.values())

        async def worker(case):
            nonlocal remaining
            name = case["catalog_agent"]
            saved = store._folder(name)
            if not store.claim(name):
                return store.read(name)
            result = {"status": "blocked", "schema": case["static_status"], "load": "not_run", "tool_roundtrip": "not_run", "response_submissions": 0}
            report = None
            started = time.monotonic()
            try:
                if case["static_status"] != "passed":
                    raise ValueError("schema_failed")
                if case.get("prompt_status") == "documented_task_input_missing":
                    raise ValueError("documented_task_input_missing; no generic utility substitution allowed")
                if args.mode == "live" and case["lane"] not in args.allow_lanes:
                    raise ValueError("resource_lane_not_approved; no deployment or invocation attempted")
                if args.mode == "live" and remaining <= 0:
                    result.update(status="not_run", reason="Invocation limit reached; no deployment attempted")
                    return result
                source = yaml.safe_load((args.repo_root / "agents" / name / "agent.yaml").read_text(encoding="utf-8"))
                bindings, expected_tools = resolve_bindings(source, args.repo_root / "agents" / name, resources)
                payload, overlays = build_agent_payload(source, args.model, bindings)
                if args.agent_prefix:
                    payload["name"] = args.agent_prefix + source["name"]
                    overlays["deployment_name"] = payload["name"]
                save_json(saved / "deployment-request.json", payload)
                save_json(saved / "deployment-overlays.json", overlays)
                (saved / "prompt.txt").write_text(case["candidate_prompt"], encoding="utf-8")
                result["expected_tools"] = expected_tools
                result["deployment_agent_name"] = payload["name"]
                if not expected_tools:
                    result.update(status="blocked", reason="No declared own-tool binding; native/platform capability mapping is required before a roundtrip can be proved.")
                    return result
                for resource_id in bindings.values():
                    resource = await client.request("GET", f"https://management.azure.com{resource_id}?api-version={API_VERSION}")
                    if (resource.get("properties") or {}).get("provisioningState") != "Succeeded":
                        raise ValueError("registered_tool_not_ready")
                agent_name = payload["name"]
                agent_url = f"{project_path}/agents/{quote(agent_name, safe='')}?api-version={API_VERSION}"
                if agent_name in deployed:
                    agent = await client.request("GET", agent_url)
                    save_json(saved / "observed-agent.json", agent)
                    replace = agent_name in getattr(args, "replace_agents", set())
                    if not matches_deployed(payload, agent) and not replace:
                        raise ValueError("existing_agent_differs_from_catalog; not overwritten")
                    if replace and (not matches_deployed(payload, agent) or agent.get("provisioningState") == "Failed"):
                        if args.mode != "live":
                            result.update(status="needs_deployment", reason="Approved test-agent replacement needed; preflight made no writes.")
                            return result
                        save_json(saved / "deployment-before.json", agent)
                        result.update(status="started", deployment_attempted=True, remote_may_be_running=True)
                        store.save(name, result)
                        try:
                            await client.request("POST", f"{project_path}:upsertAgent?api-version={API_VERSION}", payload)
                        except asyncio.TimeoutError:
                            result["deployment_request_timed_out"] = True
                        async with asyncio.timeout(180):
                            while True:
                                agent = await client.request("GET", agent_url)
                                if agent.get("provisioningState") in {"Succeeded", "Failed", "Canceled"}:
                                    break
                                await asyncio.sleep(3)
                        result["remote_may_be_running"] = False
                elif args.mode == "preflight":
                    result.update(status="needs_deployment", reason="Catalog definition and tool bindings prepared; agent is not loaded.")
                    return result
                elif not args.deploy_missing:
                    raise ValueError("agent_missing; deployment was not authorized")
                else:
                    result.update(status="started", deployment_attempted=True)
                    store.save(name, result)
                    # Once the write is attempted, interruption is uncertain. Never retry.
                    result["remote_may_be_running"] = True
                    try:
                        await client.request("POST", f"{project_path}:upsertAgent?api-version={API_VERSION}", payload)
                    except asyncio.TimeoutError:
                        # The write may have landed; observe its state read-only instead of resubmitting.
                        result["deployment_request_timed_out"] = True
                    async with asyncio.timeout(180):
                        while True:
                            agent = await client.request("GET", agent_url)
                            state = agent.get("provisioningState")
                            if state in {"Succeeded", "Failed", "Canceled"}:
                                break
                            await asyncio.sleep(3)
                    result["remote_may_be_running"] = False
                save_json(saved / "observed-agent.json", agent)
                if agent.get("provisioningState") != "Succeeded":
                    raise ValueError("agent_load_failed")
                if not matches_deployed(payload, agent):
                    raise ValueError("deployed_definition_differs_from_request")
                result.update(load="passed", status="ready")
                if case.get("prerequisite_blocker"):
                    result.update(status="blocked", reason=case["prerequisite_blocker"])
                    return result
                if not expected_tools:
                    result.update(status="blocked", reason="No verifiable own-tool binding; native/platform adapter or applicability decision required.")
                    return result
                asset_ids, unstaged = staged_inputs(case, args.input_assets)
                if unstaged:
                    # Agents correctly refuse to invent inputs; do not spend an invocation to prove it.
                    result.update(status="blocked", reason="input_not_staged: " + ", ".join(unstaged))
                    return result
                if args.mode == "preflight":
                    return result
                if remaining <= 0:
                    result.update(status="not_run", reason="Invocation limit reached")
                    return result
                remaining -= 1  # No await between the cap check and reservation.
                run_id = uuid.uuid4().hex[:16]
                report = {"schema_version": 1, "run_id": run_id, "started_at": datetime.now(timezone.utc).isoformat(),
                          "mode": "live", "phase": "agent_preflight", "verdict": "not_run", "agent": agent_name,
                          "project": args.project, "workspace_api_url": args.workspace_api_url,
                          "contract": CONTRACT_ID, "catalog_revision": revision(), "source_fingerprints": case["source_fingerprints"],
                          "expected_tools": expected_tools, "investigation": investigation_name(agent_name, run_id=getattr(args, "run_label", None)),
                          "run_label": getattr(args, "run_label", None),
                          "prompt": tool_anchored_prompt(case["candidate_prompt"], expected_tools, case["lane"],
                                                         case.get("input_files") or ()),
                          "storage_assets": asset_ids,
                          "prompt_overlay": "documented-first-task-v4", "submission_attempted": False,
                          "server_terminal_observed": False, "client_timeout_seconds": args.timeout,
                          "max_agent_invocations": 1, "automatic_retries": 0, "server_cancellation": "not_attempted"}
                report["runner_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
                (saved / "prompt.txt").write_text(report["prompt"], encoding="utf-8")
                save_json(saved / "summary.json", report)
                result.update(status="started", remote_may_be_running=True)
                store.save(name, result)
                invocation_start = time.monotonic()
                try:
                    await asyncio.wait_for(run_live(SimpleNamespace(workspace_api_url=args.workspace_api_url,
                        project=args.project, agent=agent_name, preflight_only=False, contract="roundtrip"), report, saved), args.timeout)
                finally:
                    report["elapsed_seconds"] = round(time.monotonic() - invocation_start, 3)
                    save_json(saved / "summary.json", report)
                    result["response_submissions"] = int(report.get("submission_attempted", False))
                    result["response_id"] = report.get("response_id")
                    result["investigation"] = report["investigation"]
                response = json.loads((saved / "response.json").read_text(encoding="utf-8"))
                operations = json.loads((saved / "operations.json").read_text(encoding="utf-8"))
                decision = classify_execution(report, response, operations)
                # Read-only wait for still-running tool operations within the same time budget,
                # so resolvable work does not hold queue capacity. Never resubmits or cancels.
                deadline = invocation_start + args.timeout
                while (decision["status"] == "uncertain" and response.get("status") in TERMINAL
                       and time.monotonic() < deadline):
                    ids = sorted(set(linked_operations(response).values()))
                    if not ids or len(ids) > 4 or all((operations.get(op) or {}).get("status") in
                                                      {"Succeeded", "Failed", "Canceled"} for op in ids):
                        break  # Nothing left that could change the verdict.
                    await asyncio.sleep(15)
                    operations = {op: await client.request("GET", f"{args.workspace_api_url}/tools/projects/"
                                  f"{quote(args.project, safe='')}/operations/{op}?api-version={API_VERSION}") for op in ids}
                    save_json(saved / "operations.json", operations)
                    decision = classify_execution(report, response, operations)
                result.update(decision, usage=report.get("usage"))
                try:
                    write_diagnostics(saved)
                except (ValueError, TypeError, KeyError) as exc:
                    result["diagnostics_error"] = type(exc).__name__
                return result
            except SmokeError as exc:
                if report is not None and not report.get("submission_attempted"):
                    result["remote_may_be_running"] = False  # Rejected before any agent response was submitted.
                result.update(status="uncertain" if result.get("remote_may_be_running") else "blocked",
                              reason=exc.code, stop_new_work=exc.code in {"http_401", "http_403"})
                return result
            except (Exception, SystemExit) as exc:
                # Never export HTTP response bodies or arbitrary exceptions to CI logs.
                message = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
                result.update(status="uncertain" if result.get("remote_may_be_running") else "blocked", reason=message)
                return result
            finally:
                result["elapsed_seconds"] = round(time.monotonic() - started, 3)
                store.save(name, result)
                print(f"{name}: {result['status']}", flush=True)
        # Completed or already-submitted cases are never claimed again. Outstanding
        # work reserves its capacity while independent cases proceed, if approved.
        pending = [case for case in plan["cases"] if case["catalog_agent"] not in prior]
        new_results = await run_queue(pending, worker, concurrency=args.concurrency,
                          continue_independent=args.continue_independent,
                          held_cases=unresolved)
        return [{"catalog_agent": name, **row} for name, row in prior.items()] + new_results


def write_report(output, plan, results, mode):
    indexed = {row["catalog_agent"]: row for row in results}
    rows = [{"catalog_agent": case["catalog_agent"], "lane": case["lane"],
             **indexed.get(case["catalog_agent"], {"status": "not_run"})} for case in plan["cases"]]
    report = {"mode": mode, "total": len(rows), "counts": dict(Counter(row["status"] for row in rows)),
              "submitted_responses": sum(row.get("response_submissions", 0) for row in rows), "results": rows}
    save_json(output / "catalog-results.json", report)
    lines = ["# Catalog smoke sweep", "", f"Mode: **{mode}**; agents accounted for: **{len(rows)}**.",
             "Local/schema/preflight checks are not live passes. Blocked/not-run is not passed.", "",
             "| Agent | Status | Schema | Load | Tool roundtrip | Reason |", "|---|---|---|---|---|---|"]
    for row in rows:
        cells = [str(row.get(key) or "not_run") for key in ("catalog_agent", "status", "schema", "load", "tool_roundtrip", "reason")]
        lines.append("| " + " | ".join(re.sub(r"[|<>\r\n]", " ", cell) for cell in cells) + " |")
    (output / "catalog-results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "results"}, indent=2))
    return report


def validate_run_limits(*, timeout, max_invocations):
    """Client waiting is not job cancellation; the catalog can grow beyond 95."""
    if not 30 <= timeout <= 3600 or max_invocations < 0:
        raise ValueError("Use timeout30..3600 and a nonnegative max-invocations allowance")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--mode", choices=["plan", "preflight", "live", "reconcile"], default="plan")
    parser.add_argument("--workspace-api-url")
    parser.add_argument("--subscription")
    parser.add_argument("--resource-group", help="Explicit approved resource group; required outside offline plan mode")
    parser.add_argument("--project", choices=["weekly-agent-svc-test"], default="weekly-agent-svc-test")
    parser.add_argument("--model", default="gpt-5-mini")
    parser.add_argument("--agent-prefix", default="", help="Optional isolated deployment name prefix; original instructions and tool confirmations stay unchanged.")
    parser.add_argument("--run-label", help="Unique week/run/attempt label (1..12 alphanumeric or hyphen characters); isolates investigations")
    parser.add_argument("--concurrency", type=int, choices=range(1, 5), default=2)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--max-invocations", type=int, default=0)
    parser.add_argument("--deploy-missing", action="store_true")
    parser.add_argument("--continue-independent", action="store_true", help="With explicit approval, continue independent cases while unresolved jobs retain concurrency slots.")
    parser.add_argument("--allow-lanes", default="cpu")
    parser.add_argument("--input-assets", type=Path, help="YAML mapping of input file names to staged storage asset ARM IDs")
    args = parser.parse_args()
    args.repo_root = args.repo_root.resolve()
    if args.run_label is not None and not re.fullmatch(r"[A-Za-z0-9-]{1,12}", args.run_label):
        parser.error("Invalid run label")
    try:
        args.input_assets = load_input_assets(args.input_assets)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    if args.agent_prefix and not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,23}", args.agent_prefix):
        parser.error("Invalid deployment prefix")
    try:
        validate_run_limits(timeout=args.timeout, max_invocations=args.max_invocations)
    except ValueError as exc:
        parser.error(str(exc))
    args.allow_lanes = set(args.allow_lanes.split(","))
    if not args.allow_lanes <= set(LANES):
        parser.error("Unrecognized resource lane")
    if args.mode != "plan":
        args.workspace_api_url = validate_endpoint(args.workspace_api_url or "")
        try:
            uuid.UUID(args.subscription)
        except (ValueError, TypeError):
            parser.error("Explicit subscription UUID required")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", args.resource_group or "") or not re.fullmatch(r"[A-Za-z0-9_.-]+", args.model):
            parser.error("Invalid resource group/model")
    if args.mode == "live" and args.max_invocations < 1:
        parser.error("Live execution needs an explicit max-invocations allowance")
    if args.mode == "reconcile" and (args.output_dir / "catalog-plan.json").is_file():
        # Reconciliation reads existing responses only and must use the original
        # run plan, even when current prompt code has changed. Never rewrite it.
        plan = json.loads((args.output_dir / "catalog-plan.json").read_text(encoding="utf-8"))
    else:
        plan = build_plan(args.repo_root, generate_manifest(args.repo_root), concurrency=args.concurrency)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    identity = {"plan": plan, "endpoint": args.workspace_api_url,
                "project": args.project, "model": args.model, "agent_prefix": args.agent_prefix}
    if args.run_label:
        identity["run_label"] = args.run_label
    if args.input_assets:  # Older output folders (no attachments) keep their original fingerprint.
        identity["input_assets"] = args.input_assets
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    store = SweepStore(args.output_dir / ("live" if args.mode == "reconcile" else args.mode), fingerprint)
    save_json(args.output_dir / "catalog-plan.json", plan)
    if args.mode == "plan":
        results = [{"catalog_agent": case["catalog_agent"], "status": "not_run", "schema": case["static_status"]} for case in plan["cases"]]
    elif args.mode == "reconcile":
        results = asyncio.run(reconcile_sweep(args, plan, store))
    else:
        results = asyncio.run(execute_sweep(args, plan, store))
    report = write_report(args.output_dir, plan, results, args.mode)
    if args.mode == "live":
        return 0 if report["counts"].get("passed", 0) == report["total"] else 1
    if args.mode == "reconcile":
        return 1 if report["counts"].get("uncertain", 0) else 0
    return 0 if plan["summary"]["static_passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())