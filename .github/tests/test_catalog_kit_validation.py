"""Offline kit tests: fixtures only; no Discovery, subprocesses, or credentials."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil

import pytest
import yaml

from catalog_kit_validation import build_report, main, render_markdown


ROOT = Path(__file__).resolve().parents[2]


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    schemas = tmp_path / "docs/schemas"
    schemas.mkdir(parents=True)
    for name in ("starter-kit-schema.json", "common-schema.json"):
        shutil.copyfile(ROOT / "docs/schemas" / name, schemas / name)
    manifest = {
        "name": "demo-kit", "version": "1.0.0", "description": "Fixture kit",
        "author": {"name": "Fixture", "email": "fixture@example.org"},
        "keywords": ["testing"], "category": "Silicon", "lifecycle": "active",
        "displayName": "Demo kit", "longDescription": "A fixture kit for deterministic offline installation and handoff contract validation.",
        "agentRefs": [{"ref": "agents/first", "role": "primary", "required": True},
                      {"ref": "agents/second", "role": "supporting", "required": False}],
        "samplePrompts": [{"id": "sp-1", "title": "Fixture task", "prompt": "Transform the fixture and return its report.",
                           "expectedOutput": "A structured report."}],
    }
    write_json(tmp_path / "starter-kits/demo-kit/kit.json", manifest)
    for name in ("first", "second"):
        base = tmp_path / "agents" / name
        base.mkdir(parents=True)
        (base / "agent.yaml").write_text("kind: prompt\nname: Fixture\ntools: []\n", encoding="utf-8")
        (base / "metadata.yaml").write_text(f"name: {name}\nassociated_tools: []\n", encoding="utf-8")
    return tmp_path


def contract_for(repo: Path) -> dict:
    """Legacy v1 fixture deliberately omits mode and participants."""
    artifact = {"id": "intermediate", "producer": "agents/first", "media_type": "application/json",
                "assertions": [{"check": "exists"}, {"check": "nonempty"},
                               {"check": "json_required_keys", "expected": ["value"]}]}
    final = deepcopy(artifact)
    final.update(id="final", producer="agents/second")
    return {
        "manifest_sha256": sha(repo / "starter-kits/demo-kit/kit.json"),
        "scenario_id": "fixture-scenario", "prompt_id": "sp-1", "fixtures": [],
        "install": {"agent_refs": ["agents/first", "agents/second"],
                    "assertions": ["installed_members_equal_declared", "primary_launchable"]},
        "handoffs": [{"id": "step-1", "from": "agents/first", "to": "agents/second",
                      "artifact_id": "intermediate", "assertion": "same_artifact_sha256"},
                     {"id": "delivery", "from": "agents/second", "to": "output",
                      "artifact_id": "final", "assertion": "same_artifact_sha256"}],
        "artifacts": [artifact, final],
    }


def single_contract_for(repo: Path, participant: str = "agents/second") -> dict:
    contract = contract_for(repo)
    contract.update(mode="single_agent", participants=[participant])
    contract["artifacts"] = [dict(contract["artifacts"][1], producer=participant)]
    contract["handoffs"] = [dict(contract["handoffs"][1], **{"from": participant})]
    return contract


def with_contract(repo: Path, contract: dict | None = None) -> dict:
    write_json(repo / "contracts.json", {"schema_version": 1, "kits": {"demo-kit": contract or contract_for(repo)}})
    return build_report(repo, "contracts.json")


def mutate_manifest(repo: Path, change) -> None:
    path = repo / "starter-kits/demo-kit/kit.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    write_json(path, data)


def test_missing_contract_is_blocked_not_kit_pass(repo: Path) -> None:
    report = build_report(repo)
    row = report["kits"][0]
    assert row["static"]["status"] == "passed"
    assert row["e2e"]["status"] == "blocked"
    assert row["e2e"]["reasons"] == ["e2e_contract_missing"]
    assert report["summary"]["e2e_passed"] == report["summary"]["e2e_executed"] == 0
    assert report["agent_tests"]["status"] == "not_run"


def test_valid_contract_is_not_run_even_if_agent_results_claim_pass(repo: Path) -> None:
    write_json(repo / "agent-results.json", {"first": "passed", "second": "passed"})
    report = with_contract(repo)
    e2e = report["kits"][0]["e2e"]
    assert e2e["status"] == "not_run"
    assert e2e["contract_status"] == "valid"
    assert e2e["reasons"] == ["protected_runtime_adapter_not_supplied"]
    assert e2e["assertions_evaluated"] == 0 and not e2e["executed"]


def test_repeatable_and_portable_byte_hashes(repo: Path, tmp_path: Path) -> None:
    before = with_contract(repo)
    assert before == build_report(repo, "contracts.json")
    destination = tmp_path / "relocated"
    shutil.copytree(repo, destination)
    assert before == build_report(destination, "contracts.json")
    (repo / "agents/first/agent.yaml").write_text("name: Changed\n", encoding="utf-8")
    after = build_report(repo, "contracts.json")
    assert before["plan_sha256"] != after["plan_sha256"]
    assert before["input_sha256"] != after["input_sha256"]


@pytest.mark.parametrize("change,reason", [
    (lambda d: d.update(name="wrong-name"), "kit_name_directory_mismatch"),
    (lambda d: d["agentRefs"].append(deepcopy(d["agentRefs"][0])), "membership_duplicate_ref"),
    (lambda d: d["agentRefs"][0].update(required=False), "membership_primary_must_be_unique_and_required"),
    (lambda d: d["agentRefs"][1].update(role="primary"), "membership_primary_must_be_unique_and_required"),
    (lambda d: d.update(agentRefs="wrong"), "membership_missing"),
    (lambda d: d.update(agentRefs=[None]), "membership_entry_invalid"),
    (lambda d: d["agentRefs"][0].update(ref="agents/../../escape"), "membership_entry_invalid"),
    (lambda d: d.update(samplePrompts=[]), "schema:samplePrompts:minItems"),
])
def test_invalid_membership_and_schema_fail_closed(repo: Path, change, reason: str) -> None:
    mutate_manifest(repo, change)
    row = build_report(repo)["kits"][0]
    assert row["static"]["status"] == "failed"
    assert reason in row["static"]["errors"]
    assert row["e2e"]["status"] == "blocked"


@pytest.mark.parametrize("name", ["first", "second"])
def test_missing_required_or_optional_member_is_catalog_error(repo: Path, name: str) -> None:
    # Existing active-kit policy resolves ALL refs, not only required refs.
    shutil.rmtree(repo / "agents" / name)
    row = build_report(repo)["kits"][0]
    assert row["static"]["status"] == "failed"
    assert any(m["ref"] == "agents/" + name and m["status"] == "failed" for m in row["members"])


@pytest.mark.parametrize("content", ["[bad", "[]", '{"name":"x","name":"y"}'])
def test_bad_manifest_remains_in_inventory(repo: Path, content: str) -> None:
    (repo / "starter-kits/demo-kit/kit.json").write_text(content, encoding="utf-8")
    report = build_report(repo)
    assert report["summary"]["kits"] == 1
    assert report["summary"]["static_failed"] == report["summary"]["e2e_blocked"] == 1


def test_missing_manifest_and_empty_inventory_are_not_success(repo: Path) -> None:
    (repo / "starter-kits/demo-kit/kit.json").unlink()
    assert build_report(repo)["summary"]["static_failed"] == 1
    shutil.rmtree(repo / "starter-kits/demo-kit")
    assert "kit_inventory_empty_or_missing" in build_report(repo)["errors"]


@pytest.mark.parametrize("schema", [None, "{}", "[]", "{bad", '{"type":42}'])
def test_missing_or_broken_schema_blocks(repo: Path, schema: str | None) -> None:
    path = repo / "docs/schemas/common-schema.json"
    if schema is None:
        path.unlink()
    else:
        path.write_text(schema, encoding="utf-8")
    report = build_report(repo)
    assert "schema_configuration_invalid:common-schema.json" in report["errors"]
    assert report["summary"]["static_failed"] == 1


def test_remote_schema_reference_never_fetched(repo: Path, monkeypatch) -> None:
    import socket
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: pytest.fail("network access forbidden"))
    write_json(repo / "docs/schemas/starter-kit-schema.json", {
        "$schema": "http://json-schema.org/draft-07/schema#", "$ref": "https://example.org/not-allowed"})
    row = build_report(repo)["kits"][0]
    assert "schema_reference_unresolvable" in row["static"]["errors"]


def test_local_tool_dependency_and_native_binding_inventory(repo: Path) -> None:
    metadata = repo / "agents/first/metadata.yaml"
    metadata.write_text("name: first\nassociated_tools: [agents/first/tools/Worker_1/]\n", encoding="utf-8")
    row = build_report(repo)["kits"][0]
    assert row["static"]["status"] == "failed"
    tool = repo / "agents/first/tools/Worker_1/tool.yaml"
    tool.parent.mkdir(parents=True)
    tool.write_text("name: worker\n", encoding="utf-8")
    (repo / "agents/first/agent.yaml").write_text("name: first\ntools: [{name: web_search}]\n", encoding="utf-8")
    row = build_report(repo)["kits"][0]
    assert row["static"]["status"] == "passed"
    assert row["members"][0]["native_bindings"] == ["web_search"]
    assert row["members"][0]["runtime_availability"] == "unknown"


@pytest.mark.parametrize("content", ["[]", "bad: [", "name: ''\n", "name: first\nassociated_tools: not-a-list\n",
                                    "name: first\nassociated_tools: [missing]\nassociated_tools: []\n",
                                    "name: first\nassociated_tools: [../../outside]\n"])
def test_malformed_dependency_data_does_not_crash(repo: Path, content: str) -> None:
    (repo / "agents/first/metadata.yaml").write_text(content, encoding="utf-8")
    assert build_report(repo)["summary"]["static_failed"] == 1


def test_agent_dependencies_require_explicit_kit_installation_membership(repo: Path) -> None:
    path = repo / "agents/first/metadata.yaml"
    path.write_text("name: first\nassociated_agents: [agents/second]\n", encoding="utf-8")
    assert build_report(repo)["summary"]["static_passed"] == 1
    path.write_text("name: first\nassociated_agents: [agents/outside]\n", encoding="utf-8")
    row = build_report(repo)["kits"][0]
    assert row["static"]["status"] == "failed"
    assert "agent_dependency_not_in_kit:agents/outside" in row["members"][0]["errors"]


@pytest.mark.parametrize("change,reason", [
    (lambda c: c.update(manifest_sha256="0" * 64), "contract_manifest_hash_mismatch"),
    (lambda c: c.update(prompt_id="sp-unknown"), "contract_prompt_not_declared"),
    (lambda c: c["install"].update(agent_refs=["agents/first"]), "contract_install_members_mismatch"),
    (lambda c: c["handoffs"][0].update(to="agents/outsider"), "contract_handoff_endpoints_invalid:step-1"),
    (lambda c: c["handoffs"][0].update(artifact_id="invented"), "contract_handoff_provenance_invalid:step-1"),
    (lambda c: c["handoffs"][0].update(to="agents/first"), "contract_inter_agent_handoff_missing"),
    (lambda c: c["handoffs"][1].update(to="agents/first"), "contract_final_artifact_handoff_missing"),
    (lambda c: c["artifacts"][0].update(assertions=[{"check": "exists"}]), "contract_artifact_assertions_incomplete:intermediate"),
    (lambda c: c["artifacts"][0]["assertions"][2].update(expected="wrong-type"), "contract_assertion_expected_invalid:intermediate:json_required_keys"),
    (lambda c: c["artifacts"][0].update(producer="agents/outsider"), "contract_artifact_producer_not_member"),
    (lambda c: c["handoffs"][1].update(id="step-1"), "contract_duplicate_handoff_id"),
])
def test_semantically_invalid_contracts_are_blocked(repo: Path, change, reason: str) -> None:
    contract = contract_for(repo)
    change(contract)
    e2e = with_contract(repo, contract)["kits"][0]["e2e"]
    assert e2e["status"] == "blocked" and e2e["contract_status"] == "invalid"
    assert reason in e2e["reasons"]


@pytest.mark.parametrize("section", ["install", "handoffs", "artifacts"])
def test_no_contract_stage_can_be_omitted(repo: Path, section: str) -> None:
    contract = contract_for(repo)
    del contract[section]
    report = with_contract(repo, contract)
    assert report["errors"]
    assert report["kits"][0]["e2e"]["reasons"] == ["contracts_document_invalid"]


def test_fixture_pin_and_handoff_provenance(repo: Path) -> None:
    fixture = repo / "fixtures/input.json"
    write_json(fixture, {"value": 1})
    contract = contract_for(repo)
    contract["fixtures"] = [{"id": "source", "path": "fixtures/input.json", "sha256": sha(fixture)}]
    contract["handoffs"].insert(0, {"id": "input", "from": "input", "to": "agents/first",
                                    "artifact_id": "source", "assertion": "same_artifact_sha256"})
    assert with_contract(repo, contract)["kits"][0]["e2e"]["status"] == "not_run"
    write_json(fixture, {"value": 2})
    assert "contract_fixture_hash_mismatch:source" in with_contract(repo, contract)["kits"][0]["e2e"]["reasons"]
    contract["fixtures"][0]["path"] = "../outside.json"
    assert "contract_fixture:source:unsafe_path" in with_contract(repo, contract)["kits"][0]["e2e"]["reasons"]


def test_single_agent_kit_still_requires_output_handoff(repo: Path) -> None:
    mutate_manifest(repo, lambda d: d.update(agentRefs=d["agentRefs"][:1]))
    contract = contract_for(repo)
    contract["install"]["agent_refs"] = ["agents/first"]
    contract["artifacts"] = contract["artifacts"][:1]
    contract["handoffs"] = [{"id": "delivery", "from": "agents/first", "to": "output",
                              "artifact_id": "intermediate", "assertion": "same_artifact_sha256"}]
    assert with_contract(repo, contract)["kits"][0]["e2e"]["status"] == "not_run"


@pytest.mark.parametrize("participant", ["agents/first", "agents/second"])
@pytest.mark.parametrize("supporting_required", [False, True])
def test_single_scenario_in_multi_member_kit(repo: Path, participant: str, supporting_required: bool) -> None:
    mutate_manifest(repo, lambda d: d["agentRefs"][1].update(required=supporting_required))
    contract = single_contract_for(repo, participant)
    report = with_contract(repo, contract)
    row = report["kits"][0]
    assert row["static"]["status"] == "passed"
    assert row["e2e"]["status"] == "not_run"
    assert row["e2e"]["contract_status"] == "valid"
    assert row["e2e"]["contract"] == contract  # No fabricated participants/handoffs.
    assert len(row["members"]) == len(contract["install"]["agent_refs"]) == 2
    assert not row["e2e"]["executed"] and row["e2e"]["assertions_evaluated"] == 0
    assert report["summary"]["e2e_passed"] == report["summary"]["e2e_executed"] == 0


@pytest.mark.parametrize("change,reason", [
    (lambda c: c["install"].update(agent_refs=["agents/second"]), "contract_install_members_mismatch"),
    (lambda c: c["install"]["agent_refs"].append("agents/outside"), "contract_install_members_mismatch"),
    (lambda c: c.update(participants=["agents/outside"]), "contract_participant_not_member"),
    (lambda c: c.update(participants=["agents/first", "agents/second"]), "contract_participant_count_invalid"),
    (lambda c: c.update(participants=["agents/first"]), "contract_participants_mismatch"),
    (lambda c: c["artifacts"][0].update(producer="agents/first"), "contract_artifact_producer_not_participant"),
    (lambda c: c["handoffs"][0].update(artifact_id="invented"), "contract_final_artifact_handoff_missing"),
    (lambda c: c["handoffs"][0].update(**{"from": "agents/first"}), "contract_handoff_provenance_invalid:delivery"),
    (lambda c: c["handoffs"][0].update(to="agents/first"), "contract_handoff_endpoints_invalid:delivery"),
    (lambda c: c["handoffs"][0].update(to="agents/second"), "contract_handoff_endpoints_invalid:delivery"),
    (lambda c: c["handoffs"][0].update(to="input"), "contract_handoff_endpoints_invalid:delivery"),
    (lambda c: c["artifacts"][0].update(assertions=[{"check": "exists"}, {"check": "nonempty"}]),
     "contract_artifact_assertions_incomplete:final"),
])
def test_single_scenario_does_not_weaken_installation_or_provenance(repo: Path, change, reason: str) -> None:
    contract = single_contract_for(repo)
    change(contract)
    e2e = with_contract(repo, contract)["kits"][0]["e2e"]
    assert e2e["status"] == "blocked" and e2e["contract_status"] == "invalid"
    assert reason in e2e["reasons"]


@pytest.mark.parametrize("change", [
    lambda c: c.pop("mode"),
    lambda c: c.pop("participants"),
    lambda c: c.update(mode="auto"),
    lambda c: c.update(mode=None),
    lambda c: c.update(participants=[]),
    lambda c: c.update(participants=["agents/second", "agents/second"]),
    lambda c: c.update(participants="agents/second"),
    lambda c: c.update(participants=[None]),
    lambda c: c.update(participants=["input"]),
    lambda c: c.update(participants=["agents/Second"]),
    lambda c: c.update(participants=["agents/second "]),
    lambda c: c.update(participants=["agents/../second"]),
    lambda c: c.update(handoffs=[]),
])
def test_explicit_scenario_schema_fails_closed(repo: Path, change) -> None:
    contract = single_contract_for(repo)
    change(contract)
    report = with_contract(repo, contract)
    assert report["errors"]
    assert report["kits"][0]["e2e"]["contract_status"] == "invalid"
    assert report["kits"][0]["e2e"]["reasons"] == ["contracts_document_invalid"]


def test_single_scenario_preserves_fixture_provenance(repo: Path) -> None:
    fixture = repo / "fixtures/input.json"
    write_json(fixture, {"value": 1})
    contract = single_contract_for(repo)
    contract["fixtures"] = [{"id": "source", "path": "fixtures/input.json", "sha256": sha(fixture)}]
    contract["handoffs"].insert(0, {"id": "input", "from": "input", "to": "agents/second",
                                     "artifact_id": "source", "assertion": "same_artifact_sha256"})
    assert with_contract(repo, contract)["kits"][0]["e2e"]["status"] == "not_run"
    contract["handoffs"][0]["to"] = "agents/first"
    assert "contract_handoff_endpoints_invalid:input" in with_contract(repo, contract)["kits"][0]["e2e"]["reasons"]
    contract["handoffs"] = [dict(contract["handoffs"][0], to="output")]
    assert "contract_final_artifact_handoff_missing" in with_contract(repo, contract)["kits"][0]["e2e"]["reasons"]


def test_explicit_multi_agent_scenario_is_valid(repo: Path) -> None:
    contract = contract_for(repo)
    contract.update(mode="multi_agent", participants=["agents/second", "agents/first"])
    assert with_contract(repo, contract)["kits"][0]["e2e"]["status"] == "not_run"


def test_multi_agent_scenario_can_exclude_primary_and_required_members(repo: Path) -> None:
    for name in ("third", "fourth"):
        shutil.copytree(repo / "agents/second", repo / "agents" / name)
        mutate_manifest(repo, lambda d, name=name: d["agentRefs"].append(
            {"ref": "agents/" + name, "role": "supporting", "required": True}))
    contract = contract_for(repo)
    contract.update(mode="multi_agent", participants=["agents/second", "agents/third"])
    contract["install"]["agent_refs"].extend(["agents/third", "agents/fourth"])
    contract["artifacts"][0]["producer"] = contract["handoffs"][0]["from"] = "agents/second"
    contract["artifacts"][1]["producer"] = contract["handoffs"][1]["from"] = "agents/third"
    contract["handoffs"][0]["to"] = "agents/third"
    assert with_contract(repo, contract)["kits"][0]["e2e"]["status"] == "not_run"
    contract["participants"].append("agents/fourth")
    assert "contract_participants_mismatch" in with_contract(repo, contract)["kits"][0]["e2e"]["reasons"]


@pytest.mark.parametrize("change,reason", [
    (lambda c: c.update(participants=["agents/first"]), "contract_participant_count_invalid"),
    (lambda c: c["handoffs"][0].update(to="output"), "contract_inter_agent_handoff_missing"),
    (lambda c: c["handoffs"][0].update(to="agents/first"), "contract_inter_agent_handoff_missing"),
    (lambda c: c["handoffs"][0].update(artifact_id="final"), "contract_inter_agent_handoff_missing"),
    (lambda c: c["handoffs"][0].update(to="agents/outside"), "contract_inter_agent_handoff_missing"),
    (lambda c: c["handoffs"][1].update(to="agents/first"), "contract_final_artifact_handoff_missing"),
])
def test_multi_agent_requires_provenance_backed_cross_member_handoff(repo: Path, change, reason: str) -> None:
    contract = contract_for(repo)
    contract.update(mode="multi_agent", participants=["agents/first", "agents/second"])
    change(contract)
    e2e = with_contract(repo, contract)["kits"][0]["e2e"]
    assert e2e["status"] == "blocked" and e2e["contract_status"] == "invalid"
    assert reason in e2e["reasons"]


@pytest.mark.parametrize("participant", ["agents/first", "agents/second"])
def test_legacy_multi_member_default_still_requires_cross_member_handoff(repo: Path, participant: str) -> None:
    mutate_manifest(repo, lambda d: d["agentRefs"][1].update(required=True))
    contract = single_contract_for(repo, participant)
    del contract["mode"], contract["participants"]
    e2e = with_contract(repo, contract)["kits"][0]["e2e"]
    assert e2e["contract"] == contract
    assert e2e["status"] == "blocked"
    assert "contract_inter_agent_handoff_missing" in e2e["reasons"]
    assert "contract_required_participants_missing" in e2e["reasons"]


def test_explicit_scenario_cli_exit_and_deterministic_report(repo: Path, capsys) -> None:
    contract = single_contract_for(repo)
    before = with_contract(repo, contract)
    assert before == build_report(repo, "contracts.json")
    assert main(["--repo-root", str(repo), "--contracts", "contracts.json"]) == 0
    assert json.loads(capsys.readouterr().out) == before
    contract.update(mode="multi_agent")
    after = with_contract(repo, contract)
    assert after["plan_sha256"] != before["plan_sha256"]
    assert main(["--repo-root", str(repo), "--contracts", "contracts.json"]) == 1
    assert json.loads(capsys.readouterr().out) == after


def test_archived_kit_not_implicitly_runnable(repo: Path) -> None:
    mutate_manifest(repo, lambda d: d.update(lifecycle="archived"))
    e2e = with_contract(repo)["kits"][0]["e2e"]
    assert e2e["status"] == "blocked" and "kit_archived" in e2e["reasons"]


def test_unknown_contract_kit_is_configuration_failure(repo: Path) -> None:
    write_json(repo / "contracts.json", {"schema_version": 1, "kits": {"typo": contract_for(repo)}})
    assert "contract_unknown_kit:typo" in build_report(repo, "contracts.json")["errors"]


def test_cli_outputs_and_exit_codes(repo: Path, capsys) -> None:
    assert main(["--repo-root", str(repo)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["summary"]["e2e_blocked"] == 1
    assert main(["--repo-root", str(repo), "--format", "markdown"]) == 0
    text = capsys.readouterr().out
    assert "NOT kit E2E passes" in text and "enabled GitHub schedule" in text
    (repo / "agents/first/agent.yaml").unlink()
    assert main(["--repo-root", str(repo)]) == 1
    capsys.readouterr()
    assert main(["--repo-root", str(repo), "--contracts", "missing.json"]) == 1
    capsys.readouterr()


def test_markdown_escapes_catalog_content(repo: Path) -> None:
    report = build_report(repo)
    report["kits"][0]["kit"] = "<script>|bad\nrow"
    assert "&lt;script&gt;&#124;bad row" in render_markdown(report)


def test_no_runtime_cli_switch(repo: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--repo-root", str(repo), "--runtime-adapter", "anything"])
    assert exc.value.code == 2


def test_workflow_is_offline_read_only_and_twice_weekly() -> None:
    # BaseLoader avoids YAML 1.1 interpreting the GitHub 'on' key as a boolean.
    workflow = yaml.load((ROOT / ".github/workflows/catalog-kit-validation.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert workflow["on"]["schedule"] == [{"cron": "0 6 * * 1,4"}]
    assert "workflow_dispatch" in workflow["on"]
    assert workflow["permissions"] == {"contents": "read"}
    assert set(workflow["on"]) == {"schedule", "workflow_dispatch", "push"}
    assert workflow["on"]["push"]["branches"] == ["automation/catalog-validation-*"]
    steps = workflow["jobs"]["offline-kit-plan"]["steps"]
    checkout = next(s for s in steps if s.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"]["persist-credentials"] == "false"
    assert all("permissions" not in j and "environment" not in j for j in workflow["jobs"].values())
    text = json.dumps(workflow)
    for forbidden in ("id-token", "azure/login", "gh issue", "secrets.", "--mode live", "run_catalog_smoke.py"):
        assert forbidden not in text


def test_actual_checkout_inventory_has_no_implicit_e2e_pass() -> None:
    report = build_report(ROOT)
    expected = sorted(p.name for p in (ROOT / "starter-kits").iterdir() if p.is_dir())
    assert [r["kit"] for r in report["kits"]] == expected
    assert report["summary"]["kits"] == len(expected) > 0
    assert report["summary"]["e2e_executed"] == report["summary"]["e2e_passed"] == 0
    assert all("e2e_contract_missing" in r["e2e"]["reasons"] for r in report["kits"])