"""Offline planning only. All fixture names are declarations; no input asset is created."""

from pathlib import Path

import pytest
import yaml

import generate_smoke_manifest as manifest
import plan_catalog_smoke as planner


@pytest.fixture
def repo(tmp_path):
    folder = tmp_path / "agents" / "demo"
    folder.mkdir(parents=True)
    (folder / "agent.yaml").write_text("kind: prompt\nname: demo\nmodel: test\ninstructions: Test.\n", encoding="utf-8")
    (folder / "metadata.yaml").write_text("name: demo\n", encoding="utf-8")
    (tmp_path / "starter-kits").mkdir()
    return tmp_path


def usage(repo, body):
    path = repo / "agents/demo/README.md"
    path.write_text("## Usage\n" + body + "\n", encoding="utf-8")
    return manifest.usage_prompts(path)


def from_row(repo, row):
    item = {"name": "demo", "path": "agents/demo", "prompt_source": "README Usage", "candidate": row}
    inventory = {"agents": [item], "starter_kits": [], "schema_errors": [], "summary": {"tool_instances": 0}}
    return planner.build_plan(repo, inventory)["cases"][0]


def table(*rows):
    return "\n".join("| " + " | ".join(row) + " |" for row in (rows[0], ["---"] * len(rows[0]), *rows[1:]))


def config(repo, path, doc):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(doc), encoding="utf-8")


@pytest.mark.parametrize(("header", "data", "files"), [
    (("Prompt", "Input Files"), ('"Count rows"', "`left.csv`, `right.csv`, `notes.txt`"),
     ["left.csv", "right.csv", "notes.txt"]),
    (("Output", "  **INPUT   FILE(S)**  ", "User Prompt"), ("out.txt", "report.v2.csv", "Count rows"), ["report.v2.csv"]),
    (("Prompt", "Description", "Input File"), ("Count rows", "not-input.txt", "in.txt"), ["in.txt"]),
    (("Input Files", "Task", "Description"), ("`in.csv`<br />extra.txt", "Count rows", "ignored"), ["in.csv", "extra.txt"]),
    (("Description", "Example Prompt", "Output"), ("ignored.txt", "Count rows", "out.txt"), []),
])
def test_columns_are_named_not_positional(repo, header, data, files):
    row, = usage(repo, table(header, data))
    case = from_row(repo, row)
    assert case["input_files"] == files
    assert (case["documented_inputs"] is None) == (not files)
    assert case["documented_candidate"] == "Count rows"
    assert case["source_path"] == "agents/demo/README.md"
    assert case["source_start_line"] == case["source_end_line"] == 4
    assert case["prompt_status"] == "documented_task_selected"
    assert case["runtime_status"] == case["checks"]["load"] == case["checks"]["tool_roundtrip"] == "not_run"
    assert case["live_ready"] is False


def test_table_scope_and_expected_output_belong_to_first_row(repo):
    first, later = usage(repo, table(("Prompt", "Input Files"), (r"Count x \| y", "a.txt, a.txt"))
                         + "\n**Expected output:** one count\n\n"
                         + table(("Output", "Description"), ("ignored.txt", "Not a prompt")) + "\n\n"
                         + table(("Prompt", "Description"), ("Later task", "unrelated.csv")))
    assert first["prompt"] == "Count x | y" and first["expected_output"] == "one count"
    assert "documented_inputs" not in later and "expected_output" not in later
    case = from_row(repo, first)
    assert case["input_files"] == ["a.txt"] and case["expected_output"] == "one count"
    assert case["approved_prompt"].startswith("Count x | y\n")


@pytest.mark.parametrize("cell", [
    "", "Upload a file", "read `sample.csv` first", "directory/", "directory/sample.csv", r"..\data.txt",
    "file://example.invalid/data.txt", "[data.txt](data.txt)", "*.csv", "$(echo data.txt)", "a.csv; echo x",
    "a.csv && b.csv", "ok.txt, plus other inputs", "ok.txt,", "NUL.txt",
])
def test_unresolved_cell_blocks_entire_case_even_with_agent_map(repo, cell):
    config(repo, planner.TASK_INPUTS, {"inputs": {"demo": ["unrelated.csv"]}})
    row, = usage(repo, table(("Prompt", "Input Files", "Output"), ("Count", cell, "out.txt")))
    case = from_row(repo, row)
    assert case["documented_inputs"] == cell and case["documented_candidate"] == "Count"
    assert case["input_files"] == [] and case["approved_prompt"] is None
    assert case["prompt_status"] == "documented_task_input_missing"
    assert "documented_inputs_unresolved" in case["blockers"]
    assert case["runtime_status"] == "not_run"


@pytest.mark.parametrize("body", [
    table(("Prompt", "Input Files"), ("Count",)),
    table(("Prompt", "Input File", "Input Files"), ("Count", "a.txt", "b.txt")),
    table(("Prompt", "Input Files"), ("Count", "a.txt", "additional files required")),
])
def test_missing_or_ambiguous_input_column_value_blocks(repo, body):
    row, = usage(repo, body)
    assert from_row(repo, row)["prompt_status"] == "documented_task_input_missing"


@pytest.mark.parametrize("cell", ["None", "N/A", "No input files required"])
def test_explicit_no_inputs_needs_no_file_and_does_not_inherit_map(repo, cell):
    config(repo, planner.TASK_INPUTS, {"inputs": {"demo": ["other.csv"]}})
    row, = usage(repo, table(("Input Files", "Prompt"), (cell, "Count")))
    case = from_row(repo, row)
    assert case["input_files"] == [] and case["prompt_status"] == "documented_task_selected"


@pytest.mark.parametrize("own_files", [[], ["chosen.csv"]])
def test_curated_override_uses_only_its_own_metadata(repo, own_files):
    row, = usage(repo, table(("Prompt", "Required files"), ("Original", "original.txt"))
                 + "\n**Expected output:** original result")
    selected = {"override": True, "prompt": "Replacement", "expected_output": "new result"}
    if own_files:
        selected["input_files"] = own_files
    config(repo, planner.CURATED_TASKS, {"tasks": {"demo": selected}})
    config(repo, planner.TASK_INPUTS, {"inputs": {"demo": ["other.txt"]}})
    case = from_row(repo, row)
    assert case["documented_candidate"] == "Original" and case["documented_prompt_source"] == "README Usage"
    assert case["prompt_status"] == "curated_task_selected" and case["prompt_source"] == "curated_first_task"
    assert case["source_path"] == planner.CURATED_TASKS.as_posix()
    assert case["source_start_line"] is case["source_end_line"] is case["documented_inputs"] is None
    assert case["input_files"] == own_files and case["expected_output"] == "new result"
    assert case["approved_prompt"].startswith("Replacement\n") and "original result" not in case["approved_prompt"]


def test_generated_fallback_is_not_an_authored_or_approved_task(repo, monkeypatch):
    # Use the schema code with the repository's own schemas, not a copy of catalog definitions.
    schema_root = Path(__file__).resolve().parents[2]
    load_schema = manifest.load_schema
    monkeypatch.setattr(manifest, "load_schema", lambda _, name: load_schema(schema_root, name))
    inventory = manifest.generate_manifest(repo)
    item, = inventory["agents"]
    assert item["candidate"]["category"] == item["prompt_source"] == "generated fallback"
    assert item["documented_usage_cases"] == []
    plan = planner.build_plan(repo, inventory)
    case, = plan["cases"]
    assert case["approved_prompt"] is case["documented_candidate"] is case["documented_prompt_source"] is None
    assert case["input_files"] == [] and case["prompt_status"] == "documented_task_input_missing"
    assert not plan["execution_enabled"] and not plan["proposed_batches"][0]["dispatch_enabled"]
    assert plan["summary"]["live_not_run"] == 1 and plan["summary"]["live_passes"] == 0


def test_legacy_map_is_for_authored_tasks_without_an_input_column_only(repo):
    row, = usage(repo, table(("Prompt", "Description"), ("Count", "example.txt")))
    config(repo, planner.TASK_INPUTS, {"inputs": {"demo": ["declared.csv"]}})
    assert from_row(repo, row)["input_files"] == ["declared.csv"]
    config(repo, planner.TASK_INPUTS, {"inputs": {"demo": ["folder/declared.csv"]}})
    with pytest.raises(ValueError, match="Invalid input file list"):
        from_row(repo, row)