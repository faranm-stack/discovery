#!/usr/bin/env python3
"""Read-only cleanup report for a Discovery subscription: tools whose image registry or tag is gone.

Uses the Azure CLI session (locally) or azure/login (in Actions). Never deletes anything.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

API = "2026-02-01-preview"


def az(*args: str):
    out = subprocess.run(["az", *args, "-o", "json"], capture_output=True, text=True, shell=sys.platform == "win32")
    if out.returncode != 0:
        return None
    return json.loads(out.stdout or "null")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--subscription", required=True)
    p.add_argument("--resource-group", help="limit to one resource group")
    p.add_argument("--out", default="cleanup-report.json")
    a = p.parse_args()

    scope = f"/subscriptions/{a.subscription}" + (f"/resourceGroups/{a.resource_group}" if a.resource_group else "")
    tools = (az("rest", "--method", "get", "--url",
                f"https://management.azure.com{scope}/providers/Microsoft.Discovery/tools?api-version={API}") or {}).get("value", [])
    registries = {r["loginServer"]: r["name"] for r in (az("acr", "list") or [])}
    tag_cache: dict[tuple[str, str], list | None] = {}
    rows = []
    for t in tools:
        for infra in t.get("properties", {}).get("definitionContent", {}).get("infra", []) or []:
            img = (infra.get("image") or {}).get("acr", "")
            if not img or "/" not in img:
                continue
            server, rest = img.split("/", 1)
            if "@" in rest:
                repo, _, ref = rest.partition("@")
                kind = "digest"
            else:
                repo, _, ref = rest.partition(":")
                ref, kind = ref or "latest", "tag"
            if server not in registries:
                status = "registry_missing_or_no_access"
            else:
                key = (registries[server], repo, kind)
                if key not in tag_cache:
                    if kind == "digest":
                        tag_cache[key] = az("acr", "manifest", "list-metadata", "-r", key[0], "-n", repo,
                                            "--query", "[].digest")
                    else:
                        tag_cache[key] = az("acr", "repository", "show-tags", "-n", key[0], "--repository", repo)
                refs = tag_cache[key]
                status = "ok" if refs and ref in refs else f"{kind}_missing_or_no_access"
            rows.append({"tool": t["name"], "resource_group": t["id"].split("/")[4], "image": img, "status": status})

    bad = [r for r in rows if r["status"] != "ok"]
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump({"tools_checked": len(rows), "stale": bad}, f, indent=1)
    print(f"{len(rows)} tool images checked, {len(bad)} stale. Report: {a.out}")
    for r in bad:
        print(f"  {r['status']:<30} {r['resource_group']:<32} {r['tool']:<28} {r['image']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
