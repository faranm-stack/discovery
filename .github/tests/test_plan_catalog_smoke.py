"""Full-catalog planning tests: no network, model calls, or deployments."""

import copy
from pathlib import Path

import pytest

from generate_smoke_manifest import generate_manifest
from plan_catalog_smoke import build_plan, estimate_model_usage, resource_lane


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def manifest():
    return generate_manifest(ROOT)


def test_plan_accounts_for_every_agent_exactly_once(manifest):
    plan = build_plan(ROOT, manifest, batch_size=5, concurrency=4)
    names = [case["catalog_agent"] for case in plan["cases"]]
    expected = {agent["name"] for agent in manifest["agents"]}
    assert set(names) == expected
    assert len(names) == len(set(names)) == plan["summary"]["catalog_agents"]
    batched = [name for batch in plan["proposed_batches"] for name in batch["agents"]]
    assert sorted(batched) == sorted(names)
    assert all(len(batch["agents"]) <= 5 for batch in plan["proposed_batches"])
    assert all(batch["dispatch_enabled"] is False for batch in plan["proposed_batches"])
    assert plan["execution_enabled"] is False
    assert plan["summary"]["live_passes"] == 0
    assert plan["summary"]["live_not_run"] == len(expected)


def test_curated_tasks_fill_readme_gaps_and_stay_labeled(manifest):
    plan = build_plan(ROOT, manifest)
    by_name = {case["catalog_agent"]: case for case in plan["cases"]}
    ase = by_name["ase"]
    assert ase["prompt_status"] == "curated_task_selected"
    assert ase["prompt_source"] == "curated_first_task"
    assert "EMT" in ase["candidate_prompt"] and "Cu 0.0 0.0 2.8" in ase["candidate_prompt"]
    # README-documented tasks still win over curated entries.
    assert by_name["aizynthfinder"]["prompt_status"] == "documented_task_selected"
    assert plan["summary"]["curated_tasks_selected"] >= 50
    missing = [c["catalog_agent"] for c in plan["cases"]
               if c["prompt_status"] == "documented_task_input_missing" and c["lane"] != "external_or_platform"]
    # New scaffold entries must stay blocked, not receive invented utility tasks.
    for name in missing:
        case = by_name[name]
        assert "documented_task_input_missing" in case["blockers"]
        assert case["approved_prompt"] is None
        assert case["live_ready"] is False


def test_every_agent_gets_one_capability_prompt_without_science_fixtures(manifest):
    plan = build_plan(ROOT, manifest)
    assert len({case["candidate_prompt"] for case in plan["cases"]}) == len(manifest["agents"])
    assert plan["policy"]["starter_kit_prompts_required"] is False
    for case in plan["cases"]:
        assert case["runtime_status"] == "not_run"
        assert "bounded_prompt_and_fixture" not in case["blockers"]
        assert "deterministic_output_assertion" not in case["blockers"]
        assert "hello world" not in case["candidate_prompt"].lower()
        assert case["deployment_agent_name"] in case["candidate_prompt"]
        assert case["expected_assertion"] == "one_correlated_successful_tool_result"
        assert case["checks"]["load"] == "not_run"
        assert case["checks"]["tool_roundtrip"] == "not_run"
        assert case["live_ready"] is False
    assert plan["summary"]["capability_prompt_agents"] == len(manifest["agents"])
    cases = {case["catalog_agent"]: case for case in plan["cases"]}
    assert cases["alphafold"]["prompt_status"] == "documented_task_selected"
    assert "MVLSPADKTNVKAAWGKVGAHAGEYGAEALERMFLSFPTTKTYFPHFDLSH" in cases["alphafold"]["candidate_prompt"]
    assert "do not invent attached files or start the full example workload" not in cases["alphafold"]["candidate_prompt"]
    assert "Step 2" not in cases["alphafold"]["candidate_prompt"]
    assert "PubChem" in cases["pubchem"]["candidate_prompt"]
    assert "Look up the PubChem entry for aspirin" in cases["pubchem"]["candidate_prompt"]
    # No README example: filled by a labeled curated first task, never a generic utility.
    assert cases["biopython"]["prompt_status"] == "curated_task_selected"
    assert "parse" in cases["biopython"]["candidate_prompt"].lower()


def test_gpu_and_external_agents_are_not_silently_excluded(manifest):
    plan = build_plan(ROOT, manifest)
    cases = {case["catalog_agent"]: case for case in plan["cases"]}
    assert cases["alphafold"]["lane"] == "gpu_capable"
    assert "gpu_cost_and_capacity_approval" in cases["alphafold"]["blockers"]
    assert cases["aqcat"]["lane"] == "external_or_platform"
    assert "external_or_platform_access" in cases["aqcat"]["blockers"]
    assert cases["flowblind-research-teammate-v2"]["expected_tools"] == ["flowblind-prepare", "flowblind-run-verify"]


def test_schema_errors_block_only_the_affected_case(manifest):
    manifest = copy.deepcopy(manifest)
    manifest["schema_errors"].append({"path": "agents/core-python-agent/agent.yaml", "errors": ["test failure"]})
    plan = build_plan(ROOT, manifest)
    cases = {case["catalog_agent"]: case for case in plan["cases"]}
    assert cases["core-python-agent"]["static_status"] == "failed"
    assert "schema_errors" in cases["core-python-agent"]["blockers"]
    assert cases["pubchem"]["static_status"] == "passed"


def test_resource_lane_is_conservative_for_unknown_compute():
    assert resource_lane([]) == "external_or_platform"
    assert resource_lane([{"infra": [{"compute": {}}]}]) == "resource_review"
    cpu = {"infra": [{"compute": {"min_resources": {"gpu": 0}, "max_resources": {"gpu": 0}}}]}
    assert resource_lane([cpu]) == "cpu"
    gpu = copy.deepcopy(cpu)
    gpu["infra"][0]["compute"]["max_resources"]["gpu"] = 1
    assert resource_lane([gpu]) == "gpu_capable"


def test_model_estimate_separates_cached_tokens_and_never_invents_unknown_rates():
    usage = {"input_tokens": 6858, "input_tokens_details": {"cached_tokens": 1920}, "output_tokens": 1316}
    rates = {"input_per_million": 0.25, "cached_input_per_million": 0.03, "output_per_million": 2}
    assert estimate_model_usage(usage, rates) == pytest.approx(0.0039241)
    assert estimate_model_usage(usage, None) is None
    assert estimate_model_usage({}, rates) is None
    assert estimate_model_usage({**usage, "input_tokens_details": {}}, rates) is None
    with pytest.raises(ValueError):
        estimate_model_usage({**usage, "input_tokens": 1}, rates)


@pytest.mark.parametrize("batch_size,concurrency", [(0, 1), (5, 0), (5, 5)])
def test_invalid_or_unapproved_parallelism_is_rejected(manifest, batch_size, concurrency):
    with pytest.raises(ValueError):
        build_plan(ROOT, manifest, batch_size=batch_size, concurrency=concurrency)