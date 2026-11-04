"""Explicit sample-prompt routing plan. No runtime, implicit delegation or passes."""
import argparse
import hashlib
import json
from pathlib import Path

# Reviewed test routing, NOT a claim about Studio's launch-button behavior.
ROUTES = {
    "chemistry-and-materials": [
        (["aizynthfinder"], "Verify target identity; ranked nonempty routes, intermediates and feasibility basis"),
        (["autodock"], "Prepared ligand/receptor and box required; poses, scores and residue interactions"),
        (["psi4"], "Verified input geometry; requested method/basis, convergence, energy and bond lengths"),
        (["mattergen"], "Five parseable candidate structures; composition and limits of stability claims"),
    ],
    "chip-synthesis-timing": [
        (["rtl-yosys-syn", "opensta"], "Verified RTL; netlist and area/cells; same netlist hash to timing; declared constraints and Fmax assumptions"),
        (["opensta"], "Verified netlist and libraries; 10 ns clock; setup/hold endpoints, WNS and TNS"),
        (["rtl-yosys-syn", "opensta"], "Verified RTL and SDC; GF180MCU libraries; netlist/SDC hash continuity; critical paths"),
        (["rtl-yosys-syn", "opensta"], "Two separately traced constraints; corresponding netlist/SDC handoffs and area/WNS/TNS comparison"),
        (["rtl-yosys-syn", "opensta"], "Verified RTL; netlist handoff; 200 MHz constraints and traceable worst setup paths"),
    ],
    "life-sciences": [
        (["alphafold"], "Verified human lysozyme sequence/isoform; parseable structure, per-residue confidence and coverage"),
        (["autodock"], "Prepared ligand/receptor and box required; poses, scores and residue interactions"),
        (["chembl", "pubchem"], "Matched compound identifiers; source assay records, target, units; predicted values never relabelled measured"),
        (["pubmed", "clinical-trials"], "Shared query/date-cutoff specification; publication identifiers and trial registry identifiers/status dates"),
    ],
    "physics-cfd-sim": [
        (["openfoam"], "Declared fluid properties, boundary conditions and mesh; convergence, pressure drop and units"),
        (["openfoam"], "Fan geometry and operating conditions required; convergence, pressure rise, torque, efficiency and mass flow"),
        (["openfoam"], "Body geometry/domain/boundary fixture required; mesh/convergence and field statistics/forces"),
    ],
}


def build_matrix(root):
    root = Path(root)
    inventory = {p.name for p in (root / "agents").iterdir() if p.is_dir()}
    rows = []
    memberships = set()
    for path in sorted((root / "starter-kits").glob("*/kit.json")):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        kit = manifest["name"]
        members = {x["ref"].split("/")[-1] for x in manifest["agentRefs"]}
        if not members <= inventory:
            raise ValueError("Kit member absent from pinned catalog")
        memberships |= members
        prompts = manifest["samplePrompts"]
        if kit not in ROUTES or len(prompts) != len(ROUTES[kit]):
            raise ValueError("New kit/sample requires routing review")
        scenarios = []
        for index, (sample, (participants, checks)) in enumerate(zip(prompts, ROUTES[kit]), 1):
            if sample["id"] != f"sp-{index}" or not set(participants) <= members:
                raise ValueError("Prompt order or participant membership changed")
            scenarios.append({"prompt_id": sample["id"], "prompt": sample["prompt"],
                "prompt_sha256": hashlib.sha256(sample["prompt"].encode()).hexdigest(),
                "expected_output": sample["expectedOutput"], "participants": participants,
                "mode": "multi_agent" if len(participants) > 1 else "single_agent",
                "routing_basis": "explicit_harness_test_plan_not_manifest_dispatch_metadata",
                "assertions_to_implement": checks, "status": "not_run",
                "readiness": "blocked_until_fixture_contract_and_runtime_adapter_verified",
                "executed": False})
        rows.append({"kit": kit, "manifest_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "members": sorted(members), "required_members": [x["ref"] for x in manifest["agentRefs"] if x["required"]],
            "installation_status": "not_tested", "scenarios": scenarios})
    if set(ROUTES) != {r["kit"] for r in rows}:
        raise ValueError("Expected kit missing")
    return {"schema_version": 1, "execution": "none", "catalog_entries": len(inventory),
        "kit_member_unique_count": len(memberships), "non_kit_agents": sorted(inventory - memberships),
        "schedule_policy": "agent coverage and kit suite each Monday/Thursday; not verified enabled",
        "catalog_coverage_policy": "Every catalog entry remains in separate agent results; membership never implies pass",
        "kits": rows}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo-root", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    report = build_matrix(a.repo_root)
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"catalog_entries": report["catalog_entries"], "kits": len(report["kits"]),
        "scenarios": sum(len(k["scenarios"]) for k in report["kits"]), "runtime_executions": 0}))


if __name__ == "__main__":
    main()