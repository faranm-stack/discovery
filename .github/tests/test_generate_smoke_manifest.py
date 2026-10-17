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