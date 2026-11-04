#!/usr/bin/env python3
"""Content-free agent/call/operation links and opt-in, bounded Azure log queries.

By default this reads saved evidence and generates KQL only. --fetch-logs queries
existing Log Analytics tables; it does not enable ingestion or invoke agents.
Auxiliary queries may be billed for scanned data. Raw rows stay in local evidence,
not terminal output or the GitHub job summary. No prompt, arguments, token,
reasoning, or tool output is copied into the correlation report.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _time(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Evidence timestamps must have a timezone")
    return parsed.astimezone(timezone.utc)


def _duration(start, end):
    try:
        seconds = (_time(end) - _time(start)).total_seconds()
        return round(seconds, 3) if seconds >= 0 else None
    except (ValueError, TypeError, AttributeError):
        return None


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", value):
        raise ValueError("Invalid identifier in diagnostic evidence")
    return value


def build_diagnostics(summary, response, operations):
    if response and response.get("id") != summary.get("response_id"):
        raise ValueError("Response does not match the run summary")
    start = _time(summary["started_at"])
    elapsed = float(summary.get("elapsed_seconds", 0))
    if not 0 <= elapsed <= 1800:
        raise ValueError("Automatic diagnostic time window exceeds 30 minutes")
    end = start + timedelta(seconds=elapsed)
    timespan = "/".join(t.isoformat().replace("+00:00", "Z") for t in (start - timedelta(minutes=5), end + timedelta(minutes=5)))
    props = response.get("discoveryProperties") or {}
    extensions = props.get("itemExtensions") or {}
    items = response.get("output") or []
    calls = [item for item in items if item.get("type") == "function_call"]
    outputs = [item for item in items if item.get("type") == "function_call_output"]
    rows = []
    break_at = summary.get("phase") if summary.get("error") else None
    if response.get("error") or response.get("status") in {"failed", "incomplete", "cancelled", "canceled"}:
        break_at = break_at or "agent_response"
    if summary.get("submission_attempted") and response and not calls:
        break_at = break_at or "tool_selection"
    for call in calls:
        call_id = call.get("call_id")
        matched = [output for output in outputs if call_id and output.get("call_id") == call_id]
        operation_id = (extensions.get(call.get("id")) or {}).get("supercomputerOperationId")
        operation = operations.get(operation_id) or {}
        if operation and operation.get("id") != operation_id:
            raise ValueError("Operation does not match its service-supplied link")
        result = operation.get("result") or {}
        if len(matched) != 1:
            break_at = break_at or "tool_result"
        elif operation_id and not operation:
            break_at = break_at or "operation_evidence"
        elif operation.get("status") in {"Failed", "Canceled"} or operation.get("error"):
            break_at = break_at or "tool_execution"
        elif operation_id and operation.get("status") != "Succeeded":
            break_at = break_at or "tool_execution_pending"
        rows.append({
            "tool": call.get("name"), "call_id": call_id, "response_item_id": call.get("id"),
            "result_count": len(matched), "operation_id": operation_id,
            "operation_status": operation.get("status"),
            "execution_seconds": _duration(result.get("createdAt"), result.get("completedAt")),
            "result_present_is_execution_proof": False,
        })
    return {
        "schema_version": 1, "run_id": summary.get("run_id"), "agent": summary.get("agent"),
        "project": summary.get("project"), "conversation_id": summary.get("conversation_id"),
        "response_id": response.get("id"), "response_status": response.get("status"),
        "correlation_id": props.get("correlationId"), "timespan": timespan,
        "http_events": [{key: event.get(key) for key in ("phase", "method", "status", "started_at", "response_headers_ms", "request_id", "correlation_id")}
                        for event in summary.get("http_events", [])],
        "tool_calls": rows, "break_at": break_at, "logs_fetched": False,
        "usage_source": "Discovery response usage", "usage": response.get("usage"),
        "notes": ["No detected break is not a capability-test verdict; assertions are separate.",
                  "Correlation report contains no raw prompt, arguments, reasoning or log messages.",
                  "A missing operation link can be normal for a read-only platform tool."],
    }


def build_queries(report):
    if not report.get("response_id"):
        return []
    response_id = _identifier(report["response_id"])
    correlation_id = report.get("correlation_id")
    conditions = [f'Message contains "{response_id}"']
    if correlation_id:
        conditions.append(f'CorrelationId == "{_identifier(correlation_id)}"')
    operations = sorted({_identifier(call["operation_id"]) for call in report["tool_calls"] if call.get("operation_id")})
    conditions += [f'Message contains "{operation_id}"' for operation_id in operations]
    queries = [{"name": "workspace", "destination": "workspace", "table": "DiscoveryLogs_CL",
                "query": "DiscoveryLogs_CL\n| where " + " or ".join(conditions)
                + "\n| project TimeGenerated, CorrelationId, LogLevel, Message\n| order by TimeGenerated asc\n| take 100"}]
    for index, operation_id in enumerate(operations):
        queries += [
            {"name": f"tool-{index}", "destination": "supercomputer", "table": "DiscoveryToolLogs_CL",
             "query": f'DiscoveryToolLogs_CL\n| where toolRunId == "{operation_id}"\n| project TimeGenerated, toolName, jobName, Message\n| order by TimeGenerated asc\n| take 100'},
            {"name": f"events-{index}", "destination": "supercomputer", "table": "KubeEvents_CL",
             "query": f'KubeEvents_CL\n| where Namespace == "scorch" and Name contains "{operation_id[:16]}"\n| project TimeGenerated, KubeEventType, Reason, ObjectKind, Name, Message\n| order by TimeGenerated asc\n| take 100'},
        ]
    if len(queries) > 3:
        raise ValueError("Pilot diagnostics allow at most one compute operation and three log queries")
    return queries


def write_diagnostics(run_dir):
    def load(name):
        path = run_dir / name
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    report = build_diagnostics(load("summary.json"), load("response.json"), load("operations.json"))
    queries = build_queries(report)
    existing_queries = load("log-evidence/manifest.json")
    if isinstance(existing_queries, list) and existing_queries:
        report["log_queries"] = existing_queries
        report["logs_fetched"] = all(entry.get("http_status") == 200 and not entry.get("error_code") for entry in existing_queries)
    (run_dir / "diagnostics.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    query_dir = run_dir / "queries"
    query_dir.mkdir(exist_ok=True)
    for query in queries:
        (query_dir / f"{query['name']}.kql").write_text(query["query"] + "\n", encoding="utf-8")
    lines = ["# Smoke correlation evidence", "",
             f"- Run: {report['run_id']}", f"- Response: {report['response_id']}",
             f"- Correlation: {report['correlation_id']}", f"- Detected break: {report['break_at'] or 'none detected'}",
             "", "| Tool | call_id | Operation | Operation status | Runtime seconds |", "|---|---|---|---|---:|"]
    for call in report["tool_calls"]:
        # Keep Markdown rendered by CI free of untrusted markup from tool names.
        values = [re.sub(r"[^a-zA-Z0-9_.:\- ]", "_", str(call[key])) for key in ("tool", "call_id", "operation_id", "operation_status", "execution_seconds")]
        lines.append("| " + " | ".join(values) + " |")
    lines += ["", "This is a correlation report, not an independent scientific pass. KQL files are generated offline; log retrieval requires explicit opt-in."]
    (run_dir / "diagnostics.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


async def fetch_logs(report, queries, destinations, output):
    import aiohttp
    from azure.identity import AzureCliCredential

    # OIDC path reuses the existing credential factory. No client secrets or new login.
    import os
    if os.environ.get("GITHUB_ACTIONS") == "true":
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "utilities/agent-evaluation/evaluators"))
        from azure_credential import get_credential
        credential = get_credential()
    else:
        credential = AzureCliCredential(process_timeout=30)
    try:
        token = await asyncio.to_thread(credential.get_token, "https://api.loganalytics.io/.default")
    finally:
        credential.close()
    output.mkdir(exist_ok=False)
    records = []
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=90)) as session:
        for query in queries:
            workspace_id = str(uuid.UUID(destinations[query["destination"]]))
            url = f"https://api.loganalytics.azure.com/v1/workspaces/{workspace_id}/search"
            print(f"Read-only Auxiliary query {query['name']}; timespan={report['timespan']}:\n{query['query']}")
            async with session.post(url, params={"timespan": report["timespan"]},
                                    json={"query": query["query"]}, allow_redirects=False,
                                    headers={"Authorization": f"Bearer {token.token}"}) as response:
                raw = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    raw.extend(chunk)
                    if len(raw) > 2 * 1024 * 1024:
                        raise ValueError("Diagnostic query exceeded 2 MiB capture limit")
                try:
                    payload = json.loads(raw)
                except ValueError:
                    payload = {}
                record = {"name": query["name"], "table": query["table"], "http_status": response.status,
                          "row_count": sum(len(table.get("rows", [])) for table in payload.get("tables", [])),
                          "row_limit_reached": any(len(table.get("rows", [])) >= 100 for table in payload.get("tables", [])),
                          "error_code": (payload.get("error") or {}).get("code"),
                          "workspace_id": workspace_id, "timespan": report["timespan"],
                          "captured_at": datetime.now(timezone.utc).isoformat(),
                          "body_sha256": hashlib.sha256(raw).hexdigest()}
                records.append(record)
                (output / "manifest.json").write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
                if response.status >= 300:
                    raise ValueError(f"Log query HTTP {response.status}; stopped without retries or permission changes.")
                (output / f"{query['name']}.json").write_bytes(raw)
                if payload.get("error"):
                    raise ValueError("Log query returned a partial error; captured evidence is incomplete.")
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--fetch-logs", action="store_true")
    parser.add_argument("--workspace-log-id")
    parser.add_argument("--supercomputer-log-id")
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    report = write_diagnostics(run_dir)
    if args.fetch_logs:
        queries = build_queries(report)
        if not queries:
            print("No submitted response to correlate; no Azure log queries made.")
            return 0
        if (run_dir / "log-evidence").exists():
            parser.error("Log evidence already exists; inspect the saved capture instead of automatically querying again")
        if not args.workspace_log_id or not args.supercomputer_log_id:
            parser.error("Explicit workspace and supercomputer Log Analytics IDs are required")
        for value in (args.workspace_log_id, args.supercomputer_log_id):
            uuid.UUID(value)
        records = asyncio.run(fetch_logs(report, queries, {
            "workspace": args.workspace_log_id, "supercomputer": args.supercomputer_log_id,
        }, run_dir / "log-evidence"))
        report["logs_fetched"] = True
        report["log_queries"] = records
        (run_dir / "diagnostics.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(records, indent=2))
    print(f"Saved correlation report; logs_fetched={report['logs_fetched']}; break_at={report['break_at']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())