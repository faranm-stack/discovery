import html
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_agent_scorecard as sc  # noqa: E402


def row(agent, status, reason):
    return {"catalog_agent": agent, "status": status, "reason": reason, "lane": "cpu",
            "usage": {"total_tokens": 10}}


def test_classify_covers_ledger_reasons():
    assert sc.classify(row("a", "passed", "Expected tool completed successfully"))["grade"] == "A"
    assert sc.classify(row("a", "passed", "own tool operation Succeeded after agent retries (1 of 4)"))["grade"] == "B"
    assert sc.classify(row("a", "failed", "own_tool_not_called"))["category"] == "tool_not_called"
    assert sc.classify(row("a", "failed", "own_tool_operations_failed (1 of 1)"))["category"] == "tool_failed"
    assert sc.classify(row("a", "failed", "The correlated tool operation failed."))["category"] == "tool_failed"
    bad_pool = sc.classify(row("a", "failed", "tool_arguments_invalid: nodePool is not a Discovery node pool"))
    assert bad_pool["category"] == "invalid_arguments" and bad_pool["fix_owner"] == "author"
    lane = sc.classify(row("a", "blocked", "resource_lane_not_approved; no deployment"))
    assert lane["grade"] == "I" and lane["fix_owner"] == "platform"
    assert sc.classify(row("a", "blocked", "agent_load_failed"))["grade"] == "F"
    assert sc.classify(row("a", "uncertain", "remote_work_not_resolved"))["grade"] == "I"


def test_eviction_needs_consecutive_failures():
    history = [{"generated_at": "1", "grades": {"x": "F"}}, {"generated_at": "2", "grades": {"x": "F"}}]
    assert sc.failing_streak("x", "F", history) == 3
    assert sc.lifecycle(3, 3) == "evict_candidate"
    broken_chain = [{"generated_at": "1", "grades": {"x": "F"}}, {"generated_at": "2", "grades": {"x": "A"}}]
    assert sc.failing_streak("x", "F", broken_chain) == 1
    assert sc.lifecycle(1, 3) == "watch"
    assert sc.failing_streak("x", "A", history) == 0


def test_platform_blockers_never_count_toward_eviction():
    card = sc.build({"results": [row("p", "blocked", "resource_lane_not_approved")]}, Path("."),
                    [{"generated_at": "1", "grades": {"p": "F"}}], 1, "t")
    assert card["agents"][0]["lifecycle"] == "keep"


def test_bots_are_never_owners():
    assert sc._is_bot("dependabot[bot]", "49699333+dependabot[bot]@users.noreply.github.com")
    assert sc._is_bot("github-actions", "actions@github.com")
    assert not sc._is_bot("Jane Doe", "123+jdoe@users.noreply.github.com")


@pytest.mark.parametrize("good_tokens", [None, 0, 10])
def test_cli_writes_report_card_and_unsent_drafts(tmp_path, good_tokens):
    repo = tmp_path / "repo"
    (repo / "agents" / "broken").mkdir(parents=True)
    (repo / "agents" / "broken" / "metadata.yaml").write_text(
        "publisher:\n  name: Example\n  contact: owner@example.com\n  party: 3p\n", encoding="utf-8")
    ledger = tmp_path / "ledger.json"
    good = row("good", "passed", "Expected tool completed successfully")
    good["usage"] = {"total_tokens": good_tokens}
    ledger.write_text(json.dumps({"results": [good,
                                              row("broken", "failed", "own_tool_not_called")]}), encoding="utf-8")
    out = tmp_path / "out"
    script = Path(__file__).resolve().parents[1] / "scripts" / "build_agent_scorecard.py"
    result = subprocess.run([sys.executable, str(script), "--ledger", str(ledger), "--repo-root", str(repo),
                             "--output-dir", str(out), "--run-label", "test"],
                            capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 0, result.stderr
    card = json.loads((out / "agent-scorecard.json").read_text(encoding="utf-8"))
    assert card["totals"]["grades"] == {"A": 1, "F": 1}
    assert card["totals"]["pass_rate_of_judged"] == 0.5
    assert card["totals"]["tokens"] == (None if good_tokens is None else 10 + good_tokens)
    assert card["totals"]["known_tokens"] == 10 + (good_tokens or 0)
    assert card["totals"]["usage_reported_agents"] == (1 if good_tokens is None else 2)
    assert card["totals"]["usage_unknown_agents"] == (1 if good_tokens is None else 0)
    assert card["agents"][1]["tokens"] == good_tokens
    summary = json.loads(result.stdout)
    assert all(summary[key] == value for key, value in card["totals"].items())
    assert "<table>" in (out / "agent-scorecard.html").read_text(encoding="utf-8")
    drafts = list((out / "author-notices").glob("*.md"))
    assert len(drafts) == 1
    text = drafts[0].read_text(encoding="utf-8")
    assert text.startswith("DRAFT, NOT SENT. To: owner@example.com") and "broken" in text
    digest = (out / "notify-digest.md").read_text(encoding="utf-8")
    assert digest.startswith("To: @faranm-stack") and "Relay to authors: OFF" in digest
    assert card["evict_after_failing_runs"] == 4


# ---- stress and edge cases ----

@pytest.mark.parametrize("usage_fields", [
    {}, {"usage": None}, {"usage": {}}, {"usage": {"total_tokens": None}},
    {"usage": {"input_tokens": 12, "output_tokens": 3}},
])
def test_missing_usage_is_unknown_not_zero(tmp_path, usage_fields):
    item = row("missing", "passed", "ok")
    del item["usage"]
    item.update(usage_fields)
    card = sc.build({"results": [item]}, tmp_path, [], 4, "t")
    assert card["agents"][0]["tokens"] is None
    assert card["totals"]["known_tokens"] == 0
    assert card["totals"]["usage_reported_agents"] == 0
    assert card["totals"]["usage_unknown_agents"] == 1
    assert card["totals"]["tokens"] is None


@pytest.mark.parametrize("values,known,reported,unknown,total", [
    ([None, None], 0, 0, 2, None),
    ([0], 0, 1, 0, 0),
    ([0, 0], 0, 2, 0, 0),
    ([0, None], 0, 1, 1, None),
    ([10, None, 0, 20], 30, 3, 1, None),
    ([10, 0, 20], 30, 3, 0, 30),
])
def test_token_totals_and_rendering(tmp_path, values, known, reported, unknown, total):
    rows = [{**row(f"agent-{i}", "passed", "ok"), "usage": {"total_tokens": value}}
            for i, value in enumerate(values)]
    card = sc.build({"results": rows}, tmp_path, [], 4, "t")
    assert [a["tokens"] for a in card["agents"]] == values
    assert card["totals"]["known_tokens"] == known
    assert card["totals"]["usage_reported_agents"] == reported
    assert card["totals"]["usage_unknown_agents"] == unknown
    assert card["totals"]["tokens"] == total
    # JSON null/zero and existing keys survive serialization without coercion.
    assert json.loads(json.dumps(card)) == card
    coverage = "partial" if unknown else "complete"
    full = "Unknown" if total is None else str(total)
    markdown, page = sc.render_markdown(card), sc.render_html(card)
    for rendered in (markdown, page):
        assert f"Known token sum ({coverage}): {known}. Full token total: {full}." in rendered
        assert f"Usage reported: {reported}/{len(values)} agents; usage unknown: {unknown}." in rendered
    agent_lines = [line for line in markdown.splitlines() if line.startswith("| agent-")]
    for line, value in zip(agent_lines, values):
        label = "Unknown" if value is None else str(value)
        assert line.endswith(f"| {label} |")
        assert f"<td>{label}</td></tr>" in page


@pytest.mark.parametrize("missing_agent_tokens", [False, True])
def test_renderers_accept_legacy_and_grader_failure_cards(tmp_path, missing_agent_tokens):
    card = sc.build({"results": [row("x", "blocked", "grader_failed")]}, tmp_path, [], 4, "t")
    for key in ("known_tokens", "usage_reported_agents", "usage_unknown_agents", "tokens"):
        del card["totals"][key]
    if missing_agent_tokens:
        del card["agents"][0]["tokens"]
    else:
        card["agents"][0]["tokens"] = None
    for renderer in (sc.render_markdown, sc.render_html):
        text = renderer(card)
        assert "Known token sum (partial): 0. Full token total: Unknown." in text
        assert "Usage reported: 0/1 agents; usage unknown: 1." in text


def test_resource_fields_and_roundtrip_grade_text_are_preserved(tmp_path, monkeypatch):
    import catalog_resource_labels
    fields = {"resource_label": "CPU declared", "resource_kind": "cpu"}
    monkeypatch.setattr(catalog_resource_labels, "resource_fields", lambda repo, name: fields)
    card = sc.build({"results": [row("x", "passed", "ok"),
                                 row("y", "failed", "own_tool_not_called")]}, tmp_path, [], 4, "t")
    for agent in card["agents"]:
        assert all(agent[key] == value for key, value in fields.items())
    for renderer in (sc.render_markdown, sc.render_html):
        text = renderer(card)
        assert "CPU declared" in text
        assert "Roundtrip passed" in text and "Roundtrip failed" in text
        assert catalog_resource_labels.RESOURCE_NOTE in html.unescape(text)


def test_input_blockers_are_harness_gaps_not_author_failures():
    for reason in ("input_not_staged: 1UBQ.pdb", "input_missing_reported_by_agent (own_tool_not_called)"):
        verdict = sc.classify(row("x", "failed" if "reported" in reason else "blocked", reason))
        assert verdict == {**verdict, "grade": "I", "category": "input_not_staged", "fix_owner": "harness"}


@pytest.mark.parametrize("text,expected", [
    ("Blocker: the required SBML model file is not present in the workspace resources.", True),
    ("I cannot run fpocket because there is no PDB input available in the workspace.", True),
    ("Blocked - required input missing. I can't run Yosys because there is no Verilog source.", True),
    ("Step 1 - High-level plan (no code). 1. Verify node pools. 2. Run LAMMPS.", False),
    ("The docking finished; the top pose scored -12.4.", False),
    ("", False),
])
def test_missing_input_detection(text, expected):
    assert sc.reports_missing_input(text) is expected


def test_evidence_relabels_only_the_matching_response(tmp_path):
    folder = tmp_path / "run-a" / "live" / "dssp"
    folder.mkdir(parents=True)
    said = {"type": "message", "content": [{"text": "Blocker: no input PDB available in the workspace."}]}
    (folder / "response.json").write_text(json.dumps({"id": "resp_1", "output": [said]}), encoding="utf-8")
    base = {**row("dssp", "failed", "own_tool_not_called"), "response_id": "resp_1"}
    assert sc.refine_with_evidence(base, tmp_path)["reason"].startswith("input_missing_reported_by_agent")
    other = {**base, "response_id": "resp_other"}
    assert sc.refine_with_evidence(other, tmp_path)["reason"] == "own_tool_not_called"
    assert sc.refine_with_evidence(base, None)["reason"] == "own_tool_not_called"


def test_four_consecutive_broken_runs_make_an_eviction_candidate():
    history = [{"generated_at": str(i), "grades": {"x": "F"}} for i in range(3)]
    assert sc.lifecycle(sc.failing_streak("x", "F", history), 4) == "evict_candidate"
    assert sc.lifecycle(sc.failing_streak("x", "F", history[1:]), 4) == "at_risk"
    # A run where the agent was absent or only blocked by the platform breaks the streak.
    gap = [{"generated_at": "0", "grades": {"x": "F"}}, {"generated_at": "1", "grades": {}},
           {"generated_at": "2", "grades": {"x": "F"}}]
    assert sc.failing_streak("x", "F", gap) == 2


def test_history_skips_corrupt_files_and_the_same_run(tmp_path):
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"run_label": "r1", "generated_at": "1", "agents": [{"agent": "x", "grade": "F"}]}))
    same = tmp_path / "same.json"
    same.write_text(json.dumps({"run_label": "r2", "generated_at": "2", "agents": [{"agent": "x", "grade": "F"}]}))
    (tmp_path / "bad.json").write_text("{not json")
    (tmp_path / "shape.json").write_text(json.dumps({"agents": [{"name": "x"}]}))
    runs = sc.load_history(sorted(tmp_path.glob("*.json")), current_label="r2")
    assert runs == [{"generated_at": "1", "grades": {"x": "F"}}]


@pytest.mark.parametrize("ledger,message", [
    ({}, "no results"),
    ({"results": []}, "no results"),
    ({"results": [{"status": "passed"}]}, "catalog_agent"),
    ({"results": [row("a", "passed", "ok"), row("a", "failed", "own_tool_not_called")]}, "Duplicate"),
])
def test_bad_ledgers_fail_loudly(ledger, message):
    with pytest.raises(ValueError, match=message):
        sc.build(ledger, Path("."), [], 4, "t")


def test_untrusted_text_is_escaped_in_every_output(tmp_path):
    evil = row("x", "failed", "own_tool_not_called <script>alert(1)</script> | @everyone\nnext")
    card = sc.build({"results": [evil]}, tmp_path, [], 4, "t")
    page = sc.render_html(card)
    assert "<script>alert" not in page and "&lt;script&gt;" in page
    table = sc.render_markdown(card)
    assert table.count("\n| x |") == 1 and "| @everyone" not in table


def test_relay_controls_mentions(tmp_path):
    a = sc.build({"results": [row("x", "failed", "own_tool_not_called")]}, tmp_path, [], 4, "t")
    a["agents"][0]["owner"]["github_login"] = "author1"
    off = sc.write_digest(a, tmp_path, "faranm-stack", relay=False).read_text(encoding="utf-8")
    assert "@author1" not in off and "`author1`" in off
    on = sc.write_digest(a, tmp_path, "faranm-stack", relay=True).read_text(encoding="utf-8")
    assert "@author1" in on and "Relay to authors: ON" in on


def test_full_catalog_scale():
    rows = [row(f"agent-{i:03d}", ("passed", "failed", "blocked")[i % 3],
                ("ok", "own_tool_operations_failed (1 of 1)", "resource_lane_not_approved")[i % 3]) for i in range(300)]
    card = sc.build({"results": rows}, Path("."), [], 4, "t")
    assert card["totals"]["agents"] == 300
    assert card["totals"]["grades"] == {"A": 100, "F": 100, "I": 100}
    assert card["totals"]["pass_rate_of_judged"] == 0.5
