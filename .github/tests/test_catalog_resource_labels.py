import pytest
from catalog_resource_labels import declared_requirement, resource_fields


def tool(lo=0, hi=0):
    return {"infra": [{"compute": {"min_resources": {"gpu": lo}, "max_resources": {"gpu": hi}}}]}


@pytest.mark.parametrize("tools,expected", [
    ([], "external_platform"), ([tool()], "no_gpu_declared"),
    ([tool(0, 1)], "gpu_optional"), ([tool(1, 8)], "gpu_required"),
    ([tool(), tool(1, 1)], "gpu_required"),
    ([{}], "unknown"), ([tool(None, 1)], "unknown"),
    ([tool(0, "1")], "unknown"), ([tool(True, 1)], "unknown"),
    ([tool(-1, 1)], "unknown"), ([tool(2, 1)], "unknown"),
    ([tool(0, float("nan"))], "unknown"), ([tool(0, 0.5)], "unknown"),
    ([tool(1, 1), {}], "unknown"),
])
def test_resource_declaration_categories(tools, expected):
    assert declared_requirement(tools) == expected


def test_missing_source_is_not_cpu_or_external(tmp_path):
    fields = resource_fields(tmp_path, "missing")
    assert fields["resource_requirement"] == "unknown"
    assert fields["observed_gpu_use"] == "not_assessed"


def test_source_provenance_and_no_execution_inference(tmp_path):
    folder = tmp_path / "agents/demo"
    folder.mkdir(parents=True)
    (folder / "agent.yaml").write_text("name: demo")
    path = folder / "tools/demo/tool.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("infra:\n- compute:\n    min_resources: {gpu: 1}\n    max_resources: {gpu: 1}\n")
    fields = resource_fields(tmp_path, "demo")
    assert fields["resource_label"] == "GPU required"
    assert len(next(iter(fields["resource_definition_hashes"].values()))) == 64
    assert fields["observed_gpu_use"] == "not_assessed"