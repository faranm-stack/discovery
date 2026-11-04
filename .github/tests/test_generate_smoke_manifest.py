from pathlib import Path

from generate_smoke_manifest import fallback_prompt, generate_manifest, usage_prompts


ROOT = Path(__file__).resolve().parents[2]


def test_manifest_covers_catalog_and_starter_kits():
    manifest = generate_manifest(ROOT)
    assert len(manifest["agents"]) == len([path for path in (ROOT / "agents").iterdir() if path.is_dir()])
    assert len(manifest["starter_kits"]) == len([path for path in (ROOT / "starter-kits").iterdir() if path.is_dir()])
    assert all(agent["candidate"]["prompt"] for agent in manifest["agents"])


def test_openmm_usage_prompts_preserve_categories():
    prompts = usage_prompts(ROOT / "agents" / "openmm" / "README.md")
    assert len(prompts) >= 8
    assert {prompt["category"] for prompt in prompts} >= {
        "Energy Minimization",
        "Full MD Simulation",
        "Implicit Solvent",
        "Trajectory Analysis",
        "AMBER File Support",
    }


def test_tool_fallback_requires_real_invocation():
    candidate = fallback_prompt("example", ["example-tool"])
    assert "Do not only describe the tool" in candidate["prompt"]
    assert "example-tool" in candidate["prompt"]


def test_alphafold_first_usage_preserves_sequence_and_excludes_followups():
    first = usage_prompts(ROOT / "agents/alphafold/README.md")[0]
    assert first["category"] == "Step 1 — Fold a single protein"
    assert first["prompt"] == (
        "Predict the structure of the following sequence:\n\n"
        ">my_protein\nMVLSPADKTNVKAAWGKVGAHAGEYGAEALERMFLSFPTTKTYFPHFDLSH"
    )
    assert "PDB/mmCIF" in first["expected_output"]
    assert "heat-map" not in first["expected_output"]
    assert "PAE matrix" not in first["prompt"]
    assert first["source_start_line"] > 100


def test_usage_accepts_unquoted_tables_and_quoted_bullets():
    assert usage_prompts(ROOT / "agents/boltzgen/README.md")[0]["prompt"].startswith("Design a protein binder against the target in 1G13 chain A")
    assert usage_prompts(ROOT / "agents/bindingdb/README.md")[0]["prompt"].startswith("Find all ligands with Ki")


def test_usage_skips_install_commands_before_first_actual_prompt():
    prompts = usage_prompts(ROOT / "agents/patent-prior-art/README.md")
    assert prompts[0]["prompt"].startswith("Here is my independent claim")
    assert "catalog_publish_agent" not in prompts[0]["prompt"]