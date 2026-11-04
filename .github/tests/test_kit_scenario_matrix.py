import json
import pytest
from kit_scenario_matrix import ROUTES, build_matrix


def fixture(tmp_path):
    for kit, routes in ROUTES.items():
        members = sorted({n for names, _ in routes for n in names})
        for name in members:
            (tmp_path / "agents" / name).mkdir(parents=True, exist_ok=True)
        path = tmp_path / "starter-kits" / kit / "kit.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"name": kit, "agentRefs": [{"ref": "agents/"+n, "required": True} for n in members],
            "samplePrompts": [{"id": f"sp-{i+1}", "prompt": f"task {i+1}", "expectedOutput": "artifact"} for i in range(len(routes))]}))
    (tmp_path / "agents" / "outside-kit").mkdir()
    return tmp_path


def test_routes_do_not_invoke_entire_kit(tmp_path):
    report = build_matrix(fixture(tmp_path))
    chem = next(k for k in report["kits"] if k["kit"] == "chemistry-and-materials")
    assert chem["scenarios"][0]["participants"] == ["aizynthfinder"]
    assert all(s["status"] == "not_run" and not s["executed"] for k in report["kits"] for s in k["scenarios"])
    assert sum(len(k["scenarios"]) for k in report["kits"]) == 16
    assert "outside-kit" in report["non_kit_agents"]


def test_missing_member_rejected(tmp_path):
    root = fixture(tmp_path)
    (root / "agents" / "opensta").rmdir()
    with pytest.raises(ValueError, match="member absent"):
        build_matrix(root)


def test_added_sample_requires_review(tmp_path):
    root = fixture(tmp_path)
    p = root / "starter-kits/physics-cfd-sim/kit.json"
    d = json.loads(p.read_text()); d["samplePrompts"].append(d["samplePrompts"][0]); p.write_text(json.dumps(d))
    with pytest.raises(ValueError, match="routing review"):
        build_matrix(root)