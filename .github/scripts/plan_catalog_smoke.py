#!/usr/bin/env python3
"""Offline, full-catalog coverage and cost-readiness plan. Never invokes an agent.

An inventory entry, proposed group, or schema pass is not live coverage. Each
agent gets one capability-specific Hello World prompt, not a scientific benchmark.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from decimal import Decimal
from pathlib import Path

import yaml

from generate_smoke_manifest import generate_manifest
from smoke_contract import CONTRACT_ID, ROUNDTRIP_PROMPT, capability_prompt


LANES = ("cpu", "external_or_platform", "gpu_capable", "resource_review")
LANE_CONCURRENCY = {"cpu": 2, "external_or_platform": 2, "gpu_capable": 1, "resource_review": 1}
CURATED_TASKS = Path(".github") / "smoke" / "curated-first-tasks.yaml"
TASK_INPUTS = Path(".github") / "smoke" / "task-inputs.yaml"


def load_task_inputs(repo):
    path = repo / TASK_INPUTS
    if not path.is_file():
        return {}
    inputs = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("inputs") or {}
    for name, files in inputs.items():
        if not isinstance(files, list) or not all(
                isinstance(f, str) and re.fullmatch(r"[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*", f) and ".." not in f
                for f in files):
            raise ValueError(f"Invalid input file list for {name}")
    return inputs


def load_curated_tasks(repo):
    path = repo / CURATED_TASKS
    if not path.is_file():
        return {}
    tasks = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("tasks") or {}
    return {name: task for name, task in tasks.items()
            if isinstance(task, dict) and isinstance(task.get("prompt"), str) and task["prompt"].strip()}


def load_prerequisites(repo):
    """Credentials, licences or network egress the lab must provide before a fair test."""
    path = repo / CURATED_TASKS
    if not path.is_file():
        return {}
    tasks = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("tasks") or {}
    return {name: task["prerequisite"].strip() for name, task in tasks.items()
            if isinstance(task, dict) and isinstance(task.get("prerequisite"), str) and task["prerequisite"].strip()}


def resource_lane(tools):
    if not tools:
        return "external_or_platform"
    values = []
    for tool in tools:
        infra = tool.get("infra") or [{}]
        for node in infra:
            compute = node.get("compute") or {}
            for bound in ("min_resources", "max_resources"):
                values.append((compute.get(bound) or {}).get("gpu"))
    if any(type(value) in (int, float) and value > 0 for value in values):
        return "gpu_capable"
    if any(type(value) not in (int, float) or value != 0 for value in values):
        return "resource_review"
    return "cpu"


def estimate_model_usage(usage, rates):
    """Reported-token price arithmetic only; None means Unknown, never zero cost."""
    if rates is None:
        return None
    total_in = usage.get("input_tokens")
    cached = (usage.get("input_tokens_details") or {}).get("cached_tokens")
    output = usage.get("output_tokens")
    if any(value is None for value in (total_in, cached, output)):
        return None
    if any(type(value) is not int or value < 0 for value in (total_in, cached, output)) or cached > total_in:
        raise ValueError("Invalid reported token usage")
    prices = []
    for key in ("input_per_million", "cached_input_per_million", "output_per_million"):
        if rates.get(key) is None:
            return None
        price = Decimal(str(rates[key]))
        if not price.is_finite() or price < 0:
            raise ValueError("Rates must be finite and nonnegative")
        prices.append(price)
    return float(((total_in - cached) * prices[0] + cached * prices[1] + output * prices[2]) / 1_000_000)


def build_plan(repo, manifest, *, batch_size=None, concurrency=4):
    if batch_size is not None and (type(batch_size) is not int or not 1 <= batch_size <= 95):
        raise ValueError("Batch size must be between 1 and 95")
    if type(concurrency) is not int or not 1 <= concurrency <= 4:
        raise ValueError("Proposed concurrency must be between 1 and 4")
    cases = []
    curated = load_curated_tasks(repo)
    prerequisites = load_prerequisites(repo)
    task_inputs = load_task_inputs(repo)
    for item in manifest["agents"]:
        folder = repo / item["path"]
        agent = yaml.safe_load((folder / "agent.yaml").read_text(encoding="utf-8")) or {}
        tool_paths = sorted((folder / "tools").glob("*/tool.yaml"))
        tools = [yaml.safe_load(path.read_text(encoding="utf-8")) or {} for path in tool_paths]
        lane = resource_lane(tools)
        failures = [error for error in manifest["schema_errors"] if error["path"].startswith(item["path"] + "/")]
        blockers = [
            "verified_deployed_definition_and_bindings", "approved_runtime_budget",
            "approved_evidence_destination", "server_job_timeout_or_cancel_policy",
        ]
        if failures:
            blockers.append("schema_errors")
        if lane == "gpu_capable":
            blockers.append("gpu_cost_and_capacity_approval")
        elif lane == "external_or_platform":
            blockers.append("external_or_platform_access")
        elif lane == "resource_review":
            blockers.append("resource_requirements_review")
        definitions = [folder / "agent.yaml", folder / "metadata.yaml", *tool_paths]
        if (folder / "README.md").is_file():
            definitions.append(folder / "README.md")
        fingerprints = {path.relative_to(repo).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                        for path in definitions if path.is_file()}
        model = agent.get("model") or {}
        documented = item["candidate"]["prompt"] if item["prompt_source"] != "generated fallback" else None
        source = item["prompt_source"]
        status = "documented_task_selected"
        if item["name"] in curated and (not documented or curated[item["name"]].get("override") is True):
            documented = curated[item["name"]]["prompt"].strip()
            source, status = "curated_first_task", "curated_task_selected"
        expected = (curated.get(item["name"], {}).get("expected_output") if status == "curated_task_selected"
                    else item["candidate"].get("expected_output") if status == "documented_task_selected" else None)
        prompt = capability_prompt(agent, documented, expected)
        if not documented:
            blockers.append("documented_task_input_missing")
        if item["name"] in prerequisites:
            blockers.append("external_prerequisite")
        cases.append({
            "case_id": f"{item['name']}:smoke-v1", "catalog_agent": item["name"],
            "contract": CONTRACT_ID,
            "deployment_agent_name": agent.get("name"), "lane": lane,
            "model_binding_source": model.get("id") if isinstance(model, dict) else model,
            "recommended_smoke_model": "gpt-5-mini",
            "expected_tools": [tool.get("name", path.parent.name) for tool, path in zip(tools, tool_paths)],
            "platform_tool_kinds": [tool.get("kind") for tool in agent.get("tools") or []],
            "static_status": "failed" if failures else "passed", "runtime_status": "not_run",
            "checks": {"schema": "failed" if failures else "passed", "load": "not_run", "tool_roundtrip": "not_run"},
            "candidate_prompt": prompt, "prompt_source": source if documented else "missing_documented_task",
            "prompt_status": status if documented else "documented_task_input_missing",
            "source_start_line": item["candidate"].get("source_start_line"),
            "source_end_line": item["candidate"].get("source_end_line"),
            "expected_output": item["candidate"].get("expected_output"),
            "documented_inputs": item["candidate"].get("documented_inputs"),
            "input_files": list(task_inputs.get(item["name"]) or []),
            "documented_candidate": item["candidate"]["prompt"], "documented_prompt_source": item["prompt_source"],
            "recipe_status": "documented_task_selected" if documented else "missing_task", "approved_prompt": prompt if documented else None,
            "expected_assertion": "one_correlated_successful_tool_result",
            "max_response_submissions": 1, "automatic_retries": 0,
            "live_ready": False, "blockers": blockers, "source_fingerprints": fingerprints,
            **({"prerequisite_blocker": "external_prerequisite: " + prerequisites[item["name"]]}
               if item["name"] in prerequisites else {}),
        })
    # One catalog ledger, kit by kit, assigning a shared agent to its first kit.
    # Lane-aware scheduling may overlap independent cases within the limits.
    kit_rank = {}
    memberships = {}
    for kit in manifest["starter_kits"]:
        for reference in kit.get("agent_refs") or []:
            name = reference["ref"].rsplit("/", 1)[-1]
            kit_rank.setdefault(name, len(kit_rank))
            memberships.setdefault(name, []).append(kit["name"])
    cases.sort(key=lambda case: (kit_rank.get(case["catalog_agent"], len(kit_rank)), case["catalog_agent"]))
    for case in cases:
        case["starter_kits"] = memberships.get(case["catalog_agent"], [])
        case["primary_starter_kit"] = next(iter(case["starter_kits"]), None)
    names = [case["catalog_agent"] for case in cases]
    if len(set(names)) != len(names):
        raise ValueError("Duplicate catalog agent entries")
    batches = []
    if batch_size is None:
        batches.append({"batch_id": "catalog-all", "lane": "mixed", "agents": sorted(names),
                        "dispatch_enabled": False, "proposed_concurrency": concurrency})
    for lane in LANES:
        if batch_size is None:
            break
        members = sorted(case["catalog_agent"] for case in cases if case["lane"] == lane)
        for offset in range(0, len(members), batch_size):
            batches.append({
                "batch_id": f"{lane}-{offset // batch_size + 1:02d}", "lane": lane,
                "agents": members[offset:offset + batch_size], "dispatch_enabled": False,
                "proposed_concurrency": min(concurrency, LANE_CONCURRENCY[lane]),
            })
    assert sorted(name for batch in batches for name in batch["agents"]) == sorted(names)
    return {
        "schema_version": 2, "scope": "offline readiness plan; no deployments or invocations", "contract": CONTRACT_ID,
        "execution_enabled": False,
        "summary": {
            "catalog_agents": len(cases), "tool_instances": manifest["summary"]["tool_instances"],
            "starter_kits_separate": len(manifest["starter_kits"]),
            "static_passed": sum(case["static_status"] == "passed" for case in cases),
            "live_passes": 0, "live_not_run": len(cases), "live_ready": 0,
            "capability_prompt_agents": len(cases),
            "documented_tasks_selected": sum(case["prompt_status"] == "documented_task_selected" for case in cases),
            "curated_tasks_selected": sum(case["prompt_status"] == "curated_task_selected" for case in cases),
            "documented_task_inputs_missing": sum(case["prompt_status"] == "documented_task_input_missing" for case in cases),
            "unique_smoke_prompts": len({case["candidate_prompt"] for case in cases}),
            "lanes": dict(Counter(case["lane"] for case in cases)), "proposed_batches": len(batches),
        },
        "policy": {
            "recommendations_only": True, "proposed_global_concurrency": concurrency,
            "execution_strategy": "one_resumable_queue_with_lane_limits",
            "prompts_per_agent": 1, "successful_own_tool_calls_required": 1,
            "starter_kit_prompts_required": False, "scientific_correctness_required": False,
            "per_agent_fixtures_required": False, "all_tool_actions_required": False,
            "max_responses_for_one_complete_sweep": len(cases),
            "proposed_per_lane_concurrency": LANE_CONCURRENCY,
            "automatic_retries": 0, "automatic_model_escalations": 0, "llm_judges": 0,
            "stop_new_submissions_on_uncertain_remote_work": True,
            "approved_live_budget_usd": None, "dollar_cap_enforced": False,
            "model_price_rates": None, "runtime_cost_usd": None,
            "cost_components": ["model_tokens", "discovery_runtime_messages", "cpu_gpu_provisioned_time", "storage_network", "external_services", "ci_minutes", "monitor_ingestion_retention_queries"],
            "copilot_development_credits_are_separate": True,
            "coverage_rule": "Every agent requires a result or an explicit blocker; skipped/blocked is not passed.",
            "limitations": [
                "CPU-defined does not mean cheap or fast; approved resource limits still apply.",
                "GPU-capable is a declaration, not proof that the minimal case requires a GPU.",
                "One response submission can contain multiple internal model/tool calls.",
                "Client wait timeouts do not cancel remote jobs or bound spend.",
                "This proposes batches only; bulk agent deployment and dispatch are not enabled.",
                "Native/external tool result formats must expose verifiable success; unknown formats are inconclusive.",
                "Existing constrained pilot evidence is not a pass for the unchanged catalog definition.",
            ],
        },
        "cases": cases, "proposed_batches": batches,
    }


def render_markdown(plan):
    summary = plan["summary"]
    lines = ["# Full-catalog smoke readiness", "",
             "**Offline plan, not an executed sweep. Every proposed batch is disabled.**", "",
             f"Agents accounted for: **{summary['catalog_agents']}**; static passes: **{summary['static_passed']}**.",
             "Catalog-definition live passes: **0**. The separate constrained pilot is not counted as full catalog coverage.",
             f"Agents accounted for: **{summary['capability_prompt_agents']}**; first documented tasks selected: **{summary['documented_tasks_selected']}**; task/input selection still missing: **{summary['documented_task_inputs_missing']}**.",
             "Required: schema pass, successful Discovery load, one successful call to an agent's own tool with its result returned.",
             "Starter-kit prompts, domain fixtures, scientific benchmarks and testing every tool action are not required.",
             "", "## Shared instruction pattern", "", ROUNDTRIP_PROMPT,
             "", "| Resource lane | Agents | Proposed concurrency |", "|---|---:|---:|"]
    for lane in LANES:
        lines.append(f"| {lane} | {summary['lanes'].get(lane, 0)} | {min(plan['policy']['proposed_global_concurrency'], LANE_CONCURRENCY[lane])} |")
    lines += ["", "CPU is not a cost guarantee. GPU/external cases remain in scope; existing permissions and approved resource limits still apply.",
              "", "## One row per catalog agent", "",
              "| Agent | Lane | Static | Candidate source | Live state |", "|---|---|---|---|---|"]
    for case in plan["cases"]:
        lines.append(f"| {case['catalog_agent']} | {case['lane']} | {case['static_status']} | {case['prompt_source']} | not run; bindings/budget pending |")
    lines += ["", "## Proposed batches (not dispatchable)", ""]
    for batch in plan["proposed_batches"]:
        lines.append(f"- {batch['batch_id']}: {', '.join(batch['agents'])}")
    lines += ["", "Each selected prompt executes the first documented usage task with its supplied inputs. Missing tasks/inputs are not replaced by utility probes. No follow-up examples or starter-kit prompts are required.",
              "Tool listings and proposed scenarios alone do not prove roundtrip success. Schema checks are not runtime tests."]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, help="Optional report grouping only. Default is one catalog queue, not 21 batches.")
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args()
    repo = args.repo_root.resolve()
    manifest = generate_manifest(repo)
    plan = build_plan(repo, manifest, batch_size=args.batch_size, concurrency=args.concurrency)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "catalog-plan.json").write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    (args.output_dir / "catalog-plan.md").write_text(render_markdown(plan), encoding="utf-8")
    print(json.dumps(plan["summary"], indent=2))
    return 1 if manifest["schema_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())