"""Answer-only grading for catalog agents that declare no own tool."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import build_agent_scorecard as grader  # noqa: E402
from catalog_sweep import classify_answer_only  # noqa: E402

CHECK = {"description": "claim chart", "require": ["claim", r"\b(full|partial|missing)\b"],
         "forbid": [r"doi:\s*10\.\d{4,}/"], "min_chars": 20}


def response(text, status="completed", rid="r1"):
    return {"id": rid, "status": status,
            "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}]}


def test_answer_meeting_check_passes_as_answer_only():
    row = classify_answer_only({"response_id": "r1"}, response("Claim 1 element A: Full. Element B: Partial."), CHECK)
    assert row["status"] == "passed" and row["tool_roundtrip"] == "not_applicable"
    assert row["reason"].startswith("answer_only_check:")


def test_missing_pattern_forbidden_pattern_or_short_answer_fails():
    assert classify_answer_only({"response_id": "r1"}, response("Nothing relevant was found here."), CHECK)["status"] == "failed"
    bad = classify_answer_only({"response_id": "r1"}, response("Claim chart: Full. See doi: 10.1234/fake"), CHECK)
    assert bad["status"] == "failed" and "forbidden" in bad["reason"]
    assert "too short" in classify_answer_only({"response_id": "r1"}, response("claim full"), CHECK)["reason"]


def test_unverified_or_unfinished_response_is_not_judged():
    assert classify_answer_only({"response_id": "other"}, response("claim full partial"), CHECK)["status"] == "uncertain"
    assert classify_answer_only({"response_id": "r1"}, response("x", status="in_progress"), CHECK)["status"] == "uncertain"
    assert classify_answer_only({"response_id": "r1"}, response("x", status="failed"), CHECK)["status"] == "failed"


def test_answer_only_pass_is_grade_c_not_a():
    verdict = grader.classify({"status": "passed", "reason": "answer_only_check: claim chart"})
    assert verdict["grade"] == "C" and verdict["category"] == "answer_checked"
    assert grader.classify({"status": "passed", "reason": "Expected tool completed"})["grade"] == "A"


def test_grade_c_excluded_from_tool_pass_rate():
    card = {"run_label": "t", "generated_at": "now", "agents": [], "totals": {
        "agents": 2, "grades": {"A": 1, "C": 1}, "categories": {}, "pass_rate_of_judged": 1.0}}
    text = grader.render_markdown(card)
    assert "| C | Answer checked (agent has no own tool) | 1 |" in text
    no_c = grader.render_markdown({**card, "totals": {**card["totals"], "grades": {"A": 1}}})
    assert "| C |" not in no_c
