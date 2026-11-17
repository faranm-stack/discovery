#!/usr/bin/env python3
"""Sync the catalog scorecard into Azure DevOps so each failing agent has a tracked, fixable work item.

Layout: Feature (--feature) -> one parent User Story per run -> one child Task per failing/blocked agent.
Agent tasks are matched by tag ``catalog-agent:<name>`` so reruns update instead of duplicating.

Default is a dry run that prints the plan. ``--apply`` writes to ADO and needs ADO_TOKEN
(an Entra bearer token for resource 499b84ac-1321-427f-aa17-267ca6975798; no PAT).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "7.1"
FIX_GRADES = {"F"}  # B means passed after retries, so it is not a fix item
PLATFORM_OWNED_EXCLUDE = {"wiley-nexus", "synthesize-bio", "aqcat", "aqpotency", "sandboxaq"}


def load_contact(catalog: Path | None, agent: str) -> str:
    if not catalog:
        return "unknown"
    meta = catalog / agent / "metadata.yaml"
    if not meta.exists():
        return "unknown"
    for line in meta.read_text(encoding="utf-8").splitlines():
        key = line.strip().lower()
        if key.startswith(("contact:", "publisher_contact:", "email:", "owner:")):
            return line.split(":", 1)[1].strip().strip("'\"") or "unknown"
    return "unknown"


def plan(scorecard: dict, catalog: Path | None) -> list[dict]:
    items = []
    for a in scorecard["agents"]:
        if a["grade"] not in FIX_GRADES:
            continue
        name = a["agent"]
        owner = "contributor" if name in PLATFORM_OWNED_EXCLUDE else "platform"
        items.append({
            "agent": name,
            "grade": a["grade"],
            "category": a.get("category", ""),
            "reason": (a.get("reason") or "")[:600],
            "contact": load_contact(catalog, name),
            "owner": owner,
        })
    return items


class Ado:
    def __init__(self, org: str, project: str, token: str):
        self.base = f"https://dev.azure.com/{org}/{urllib.parse.quote(project)}/_apis"
        self.org_url = f"https://dev.azure.com/{org}"
        self.h = {"Authorization": f"Bearer {token}"}

    def _req(self, method: str, url: str, body=None, ctype="application/json"):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={**self.h, "Content-Type": ctype})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            sys.exit(f"ADO {method} {url} -> {e.code}: {e.read()[:300]!r}")

    def find_by_tag(self, tag: str) -> int | None:
        wiql = {"query": "SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = @project "
                         f"AND [System.Tags] CONTAINS '{tag}' AND [System.State] <> 'Removed'"}
        res = self._req("POST", f"{self.base}/wit/wiql?api-version={API}", wiql)
        ids = [w["id"] for w in res.get("workItems", [])]
        return ids[0] if ids else None

    def upsert(self, wtype: str, tag: str, title: str, html: str, parent: int | None, extra_tags: str) -> int:
        existing = self.find_by_tag(tag)
        ops = [
            {"op": "add", "path": "/fields/System.Title", "value": title},
            {"op": "add", "path": "/fields/System.Description", "value": html},
            {"op": "add", "path": "/fields/System.Tags", "value": f"{tag}; {extra_tags}"},
        ]
        if existing:
            self._req("PATCH", f"{self.base}/wit/workitems/{existing}?api-version={API}", ops,
                      "application/json-patch+json")
            return existing
        if parent:
            ops.append({"op": "add", "path": "/relations/-", "value": {
                "rel": "System.LinkTypes.Hierarchy-Reverse",
                "url": f"{self.org_url}/_apis/wit/workItems/{parent}"}})
        res = self._req("POST", f"{self.base}/wit/workitems/${urllib.parse.quote(wtype)}?api-version={API}",
                        ops, "application/json-patch+json")
        return res["id"]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--scorecard", required=True, type=Path)
    p.add_argument("--catalog", type=Path, help="agents root, for metadata.yaml contacts")
    p.add_argument("--run-label", required=True, help="e.g. catalog-run2-20261103")
    p.add_argument("--report-url", default="", help="link to the published report card")
    p.add_argument("--org", default=os.environ.get("ADO_ORG", ""))
    p.add_argument("--project", default=os.environ.get("ADO_PROJECT", ""))
    p.add_argument("--feature", type=int, default=int(os.environ.get("ADO_FEATURE_ID") or 0))
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()

    card = json.loads(args.scorecard.read_text(encoding="utf-8"))
    items = plan(card, args.catalog)
    grades = card.get("grades", {})
    print(f"Run {args.run_label}: grades {grades}; {len(items)} work items "
          f"({sum(i['owner'] == 'platform' for i in items)} platform, "
          f"{sum(i['owner'] == 'contributor' for i in items)} contributor-validated)")
    for i in items:
        print(f"  [{i['grade']}] {i['agent']:<28} owner={i['owner']:<11} contact={i['contact']:<25} {i['category']}")

    if not args.apply:
        print("Dry run. Pass --apply with ADO_TOKEN set to write.")
        return 0
    token = os.environ.get("ADO_TOKEN")
    if not token:
        sys.exit("ADO_TOKEN is not set.")
    if not (args.org and args.project and args.feature):
        sys.exit("Set --org, --project and --feature (or ADO_ORG, ADO_PROJECT, ADO_FEATURE_ID).")
    ado = Ado(args.org, args.project, token)
    story = ado.upsert(
        "User Story", f"catalog-run:{args.run_label}", f"Catalog report card: {args.run_label}",
        f"<p>Grades: {grades}</p><p>Report: {args.report_url or 'n/a'}</p>"
        "<p>Child tasks track each failing or blocked agent. Contributor-owned integrations "
        "(accounts, credits, MCP) are validated by their contributor.</p>",
        args.feature, "catalog-report-card")
    print(f"Story {story}")
    for i in items:
        html = (f"<p><b>Grade:</b> {i['grade']} &nbsp; <b>Category:</b> {i['category']}</p>"
                f"<p><b>Observed:</b> {i['reason']}</p><p><b>Contributor contact:</b> {i['contact']}</p>"
                f"<p><b>Fix owner:</b> {i['owner']}</p><p>Run: {args.run_label}. {args.report_url}</p>")
        wid = ado.upsert("Task", f"catalog-agent:{i['agent']}", f"[{i['grade']}] {i['agent']}: fix catalog run failure",
                         html, story, f"catalog-report-card; owner:{i['owner']}")
        print(f"  Task {wid} {i['agent']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
