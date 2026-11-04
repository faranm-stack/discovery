"""Targeted offline tests for the all-catalog runner; never submit a live run."""

import asyncio
import json

import pytest

from catalog_sweep import SweepStore, build_agent_payload, run_queue
import catalog_sweep


def source_agent():
    return {"kind": "prompt", "name": "Example", "description": "Example capability",
            "instructions": "Use the original tool. Never change access.",
            "model": {"id": "{{CHAT-MODEL}}", "options": {"temperature": 0, "topP": 0}},
            "discoveryExtensions": {"humanInTheLoop": "Enabled", "disableDiscoveryInjectedTools": False,
                                    "tools": [{"toolId": "{{exampleToolId}}", "confirmation": "Enabled"}]}}


def test_deployment_preserves_discovery_fields_instructions_and_confirmation():
    source = source_agent()
    source["instructions"] += " Runtime context: {{nodePoolContext}} and {{dataHandlingContext}}."
    payload, overlays = build_agent_payload(source, "gpt-5-mini", {"{{exampleToolId}}": "/subscriptions/test/tools/example"})
    assert payload["humanInTheLoop"] == "Enabled"
    assert payload["tools"][0]["confirmation"] == "Enabled"
    assert payload["discoveryExtensions"]["disableDiscoveryInjectedTools"] is False
    assert payload["foundryDetails"]["definition"]["instructions"] == source["instructions"]
    assert source["discoveryExtensions"]["tools"][0]["toolId"] == "{{exampleToolId}}"
    assert overlays["omitted_model_options"] == {"temperature": 0, "topP": 0}


def test_unechoed_model_options_match_but_changed_values_do_not():
    source = source_agent()
    source["model"]["options"]["maxOutputTokens"] = 4096
    payload, _ = build_agent_payload(source, "gpt-5-mini", {"{{exampleToolId}}": "/tools/example"})
    echoed = {k: v for k, v in payload["foundryDetails"]["definition"].items() if k != "max_output_tokens"}
    agent = {"foundryDetails": {"definition": echoed}, "tools": payload["tools"],
             "humanInTheLoop": payload["humanInTheLoop"], "discoveryExtensions": payload["discoveryExtensions"]}
    assert catalog_sweep.matches_deployed(payload, agent)
    agent["foundryDetails"]["definition"] = {**echoed, "max_output_tokens": 100}
    assert not catalog_sweep.matches_deployed(payload, agent)
    agent["foundryDetails"]["definition"] = {**echoed, "instructions": "changed"}
    assert not catalog_sweep.matches_deployed(payload, agent)


def test_unresolved_tool_and_native_connections_fail_closed():
    with pytest.raises(ValueError, match="binding"):
        build_agent_payload(source_agent(), "gpt-5-mini", {})
    source = source_agent()
    source["tools"] = [{"kind": "mcp", "connection": {"endpoint": "{{URL}}"}}]
    with pytest.raises(ValueError, match="native"):
        build_agent_payload(source, "gpt-5-mini", {"{{exampleToolId}}": "/tools/example"})


def test_durable_store_never_resubmits_started_or_uncertain_work(tmp_path):
    store = SweepStore(tmp_path, "plan-hash")
    assert store.claim("agent-a")
    assert not store.claim("agent-a")
    store.save("agent-a", {"status": "uncertain", "response_id": "resp_existing"})
    resumed = SweepStore(tmp_path, "plan-hash")
    assert not resumed.claim("agent-a")
    assert resumed.read("agent-a")["response_id"] == "resp_existing"
    (tmp_path / "agent-a/summary.json").write_text(json.dumps({"response_id": "resp_existing", "submission_attempted": True}), encoding="utf-8")
    assert resumed.read("agent-a")["response_submissions"] == 1
    with pytest.raises(ValueError, match="different"):
        SweepStore(tmp_path, "other-plan")


def test_queue_covers_every_case_and_bounds_active_work():
    async def scenario():
        active = 0
        peak = 0
        seen = []
        async def worker(case):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0)
            seen.append(case["catalog_agent"])
            active -= 1
            return {"status": "passed"}
        cases = [{"catalog_agent": f"agent-{i}", "lane": "cpu"} for i in range(95)]
        results = await run_queue(cases, worker, concurrency=4)
        assert len(results) == len(set(seen)) == 95
        assert peak <= 2  # CPU lane cap, beneath global cap.
    asyncio.run(scenario())


def test_uncertain_response_stops_new_submissions():
    async def scenario():
        seen = []
        async def worker(case):
            seen.append(case["catalog_agent"])
            return {"status": "uncertain"}
        cases = [{"catalog_agent": f"agent-{i}", "lane": "cpu"} for i in range(5)]
        results = await run_queue(cases, worker, concurrency=1)
        assert len(seen) == 1
        assert len(results) == 5
        assert sum(r["status"] == "not_run" for r in results) == 4
    asyncio.run(scenario())


def completed_helper_response(name="GetNodePoolContext"):
    return {"id": "resp_existing", "status": "completed", "output": [
        {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": name, "status": "completed"},
        {"type": "function_call_output", "call_id": "call_1", "status": "completed", "output": "Helper completed"},
    ]}


@pytest.mark.parametrize("helper", ["GetNodePoolContext", "WriteResource"])
def test_completed_helper_only_is_failed_not_an_uncertain_running_job(helper):
    response = completed_helper_response(helper)
    result = catalog_sweep.classify_execution(
        {"response_id": "resp_existing", "expected_tools": ["example"]}, response, {},
    )
    assert result["status"] == "failed"
    assert result["reason"] == "own_tool_not_called"
    assert result["remote_may_be_running"] is False


@pytest.mark.parametrize("state", ["Running", "NotStarted", None])
def test_running_or_missing_operation_remains_uncertain(state):
    response = completed_helper_response("example-Execute")
    response["discoveryProperties"] = {"itemExtensions": {"fc_1": {"supercomputerOperationId": "a" * 32}}}
    operation = {"id": "a" * 32, "status": state}
    result = catalog_sweep.classify_execution(
        {"response_id": "resp_existing", "expected_tools": ["example"]}, response,
        {"a" * 32: operation} if state else {},
    )
    assert result["status"] == "uncertain"
    assert result["remote_may_be_running"] is True


def test_completed_failure_can_continue_without_repeating_case():
    async def scenario():
        seen = []
        async def worker(case):
            seen.append(case["catalog_agent"])
            return {"status": "failed", "remote_may_be_running": False}
        cases = [{"catalog_agent": f"agent-{i}", "lane": "cpu"} for i in range(3)]
        results = await run_queue(cases, worker, concurrency=1)
        assert len(seen) == len(results) == 3
    asyncio.run(scenario())


def test_tool_anchored_prompt_excludes_helpers_as_demonstration():
    prompt = catalog_sweep.tool_anchored_prompt("Exercise the declared capability.", ["biopython"], "cpu")
    assert "biopython" in prompt
    assert "GetNodePoolContext" in prompt
    assert "do NOT satisfy the demonstration" in prompt
    assert "Do not retry" in prompt
    assert "never a GPU" in prompt
    assert "nonempty nodePool" in prompt


@pytest.mark.parametrize("correlated", [True, False])
def test_only_correlated_backend_validation_rejection_resolves_compute_uncertainty(correlated):
    response = completed_helper_response("example-Execute")
    response["output"][0]["arguments"] = '{"nodePool":""}'
    response["output"][1]["output"] = "Error: An unexpected error occurred."
    response["discoveryProperties"] = {"correlationId": "corr-1"}
    logs = {"tables": [{"columns": [{"name": name} for name in ["CorrelationId", "Exception", "LogLevel"]],
                        "rows": [["corr-1" if correlated else "other-request",
                                  "System.ArgumentException: The 'nodePool' parameter is required. Use GetNodePoolContext to retrieve available node pools.",
                                  "Error"]]}]}
    result = catalog_sweep.classify_execution(
        {"response_id": "resp_existing", "expected_tools": ["example"]}, response, {}, validation_logs=logs,
    )
    assert result["status"] == ("failed" if correlated else "uncertain")
    assert result["remote_may_be_running"] is (not correlated)


def test_pending_gpu_reserves_capacity_while_cpu_cases_continue():
    async def scenario():
        seen = []
        active = 0
        peak = 0
        async def worker(case):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            seen.append(case["catalog_agent"])
            await asyncio.sleep(0)
            active -= 1
            return {"status": "passed"}
        cases = [{"catalog_agent": "new-gpu", "lane": "gpu_capable"}] + [
            {"catalog_agent": f"cpu-{i}", "lane": "cpu"} for i in range(3)]
        results = await run_queue(cases, worker, concurrency=2, continue_independent=True,
                                  held_cases=[{"catalog_agent": "existing-gpu", "lane": "gpu_capable"}])
        assert seen == ["cpu-0", "cpu-1", "cpu-2"]
        assert peak == 1  # One of the two slots is reserved for the existing job.
        assert next(row for row in results if row["catalog_agent"] == "new-gpu")["status"] == "not_run"
    asyncio.run(scenario())


def test_two_uncertain_agents_exhaust_capacity_not_allow_unbounded_dispatch():
    async def scenario():
        seen = []
        async def worker(case):
            seen.append(case["catalog_agent"])
            return {"status": "uncertain", "remote_may_be_running": True}
        cases = [{"catalog_agent": f"cpu-{i}", "lane": "cpu"} for i in range(5)]
        results = await run_queue(cases, worker, concurrency=2, continue_independent=True)
        assert len(seen) == 2
        assert sum(row["status"] == "not_run" for row in results) == 3
    asyncio.run(scenario())
ASSET = ("/subscriptions/11111111-1111-4111-8111-111111111111/resourceGroups/rg/providers/"
         "Microsoft.Discovery/storageContainers/c1/storageAssets/a1")


def test_prompt_names_attached_inputs_and_preapproves_the_single_task():
    prompt = catalog_sweep.tool_anchored_prompt("Run DSSP on 1UBQ.", ["dssp"], "cpu", ["1UBQ.pdb"])
    assert "attached to this message as storage assets: 1UBQ.pdb" in prompt
    assert "do not ask for an upload" in prompt
    assert "pre-approved" in prompt and "instead of stopping at a plan" in prompt
    assert "writing that input is part of the task" in prompt
    assert "storage assets" not in catalog_sweep.tool_anchored_prompt("Run.", ["dssp"], "cpu")


def test_staged_inputs_reports_every_missing_file():
    case = {"input_files": ["1UBQ.pdb", "1D3Z.pdb", "1AAR.pdb"]}
    ids, missing = catalog_sweep.staged_inputs(case, {"1UBQ.pdb": ASSET})
    assert ids == [ASSET] and missing == ["1D3Z.pdb", "1AAR.pdb"]
    shared = {"1UBQ.pdb": ASSET, "1D3Z.pdb": ASSET, "1AAR.pdb": ASSET}
    assert catalog_sweep.staged_inputs(case, shared) == ([ASSET], [])
    assert catalog_sweep.staged_inputs({}, {}) == ([], [])
    assert catalog_sweep.staged_inputs({"input_files": None}, {}) == ([], [])


@pytest.mark.parametrize("value", [
    "https://evil.example/1UBQ.pdb",
    ASSET + "/../../other",
    "/subscriptions/x/resourceGroups/rg/providers/Microsoft.Storage/storageAccounts/s",
    42,
])
def test_input_assets_reject_anything_but_storage_asset_ids(tmp_path, value):
    path = tmp_path / "assets.yaml"
    path.write_text(json.dumps({"assets": {"1UBQ.pdb": value}}), encoding="utf-8")
    with pytest.raises(ValueError):
        catalog_sweep.load_input_assets(path)


def test_input_assets_accept_valid_ids_and_empty(tmp_path):
    path = tmp_path / "assets.yaml"
    path.write_text(json.dumps({"assets": {"1UBQ.pdb": ASSET}}), encoding="utf-8")
    assert catalog_sweep.load_input_assets(path) == {"1UBQ.pdb": ASSET}
    assert catalog_sweep.load_input_assets(None) == {}


@pytest.mark.parametrize("said,reason", [
    ("Blocker: there is no input PDB available in the workspace.", "input_missing_reported_by_agent (own_tool_not_called)"),
    ("Step 1 - plan. I will run LAMMPS after you approve.", "own_tool_not_called"),
])
def test_tool_not_called_separates_missing_input_from_other_stops(said, reason):
    response = completed_helper_response("GetResourceContext")
    response["output"].append({"type": "message", "content": [{"type": "output_text", "text": said}]})
    result = catalog_sweep.classify_execution(
        {"response_id": "resp_existing", "expected_tools": ["dssp"]}, response, {})
    assert result["status"] == "failed" and result["reason"] == reason


def test_every_declared_task_input_has_a_fixture_or_a_documented_producer():
    import yaml
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    config = yaml.safe_load((root / ".github/smoke/task-inputs.yaml").read_text(encoding="utf-8"))
    inputs = config["inputs"]
    external = config.get("external_inputs") or {}
    for requirement in external.values():
        if "source" in requirement:
            assert requirement["source"].startswith("https://")
        else:
            assert "review" in requirement["reason"].lower() or "verification" in requirement["reason"].lower()
        assert requirement["reason"].strip()
    produced_elsewhere = {"counter16_sky130_netlist.v"}
    for agent, files in inputs.items():
        assert (root / "agents" / agent).is_dir(), agent
        for name in files:
            assert name in produced_elsewhere or name in external or (root / ".github/smoke/inputs" / name).is_file() \
                or ("/" in name and (root / name).is_file()), name


def test_plan_carries_input_files_into_cases():
    from pathlib import Path
    from generate_smoke_manifest import generate_manifest
    from plan_catalog_smoke import build_plan
    root = Path(__file__).resolve().parents[2]
    cases = {c["catalog_agent"]: c for c in build_plan(root, generate_manifest(root), concurrency=2)["cases"]}
    assert cases["tmalign"]["input_files"] == ["1UBQ.pdb", "1D3Z.pdb"]
    assert cases["biopython"]["input_files"] == []


@pytest.mark.parametrize("entry", ["../secrets.txt", "agents/../../x", "/etc/passwd", "a b.pdb", 5])
def test_task_inputs_reject_unsafe_paths(tmp_path, entry):
    from plan_catalog_smoke import load_task_inputs
    (tmp_path / ".github" / "smoke").mkdir(parents=True)
    (tmp_path / ".github/smoke/task-inputs.yaml").write_text(json.dumps({"inputs": {"x": [entry]}}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_task_inputs(tmp_path)
