#!/usr/bin/env python3
"""Generate a deterministic, non-executing catalog smoke-test manifest."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from referencing import Registry, Resource

from catalog_validation.schemas import iter_schema_errors, load_json, load_schema


PROMPT_CELL = re.compile(r'^\|\s*["“](.+?)["”]\s*\|')
HEADING = re.compile(r"^(#{2,6})\s+(.+?)\s*$")


def load_yaml_mapping(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        document = yaml.safe_load(stream)
    if not isinstance(document, dict):
        raise ValueError("document must be a mapping")
    return document


def schema_messages(
    document: Any,
    schema: dict[str, Any],
    registry: Registry,
) -> list[str]:
    return [
        f"{'/'.join(map(str, error.absolute_path)) or '(root)'}: {error.message}"
        for error in iter_schema_errors(document, schema, registry)
    ]


def usage_prompts(readme: Path) -> list[dict[str, str]]:
    """Extract quoted prompts from Markdown tables under a Usage heading."""
    if not readme.is_file():
        return []
    prompts: list[dict[str, str]] = []
    in_usage = False
    category = "Usage"
    usage_level = 0
    for raw_line in readme.read_text(encoding="utf-8").splitlines():
        heading = HEADING.match(raw_line)
        if heading:
            level = len(heading.group(1))
            title = heading.group(2).strip()
            if title.casefold() == "usage":
                in_usage = True
                usage_level = level
                category = "Usage"
                continue
            if in_usage and level <= usage_level:
                break
            if in_usage:
                category = title
                continue
        if not in_usage:
            continue
        match = PROMPT_CELL.match(raw_line.strip())
        if match:
            prompts.append({"category": category, "prompt": match.group(1).strip()})
    return prompts


def agent_yaml_prompts(agent: dict[str, Any]) -> list[dict[str, str]]:
    values = agent.get("samplePrompts") or []
    return [
        {"category": "agent.yaml samplePrompts", "prompt": value.strip()}
        for value in values
        if isinstance(value, str) and value.strip()
    ]


def fallback_prompt(name: str, tool_names: list[str]) -> dict[str, str]:
    if tool_names:
        tools = ", ".join(tool_names)
        prompt = (
            f"Use one available action from {tools} to perform the smallest "
            "self-contained operation that requires no uploaded files, and return "
            "the tool result. Do not only describe the tool."
        )
    else:
        prompt = (
            f"Briefly demonstrate that the {name} agent is responsive by describing "
            "one supported capability and returning a concise example result."
        )
    return {"category": "generated fallback", "prompt": prompt}


def generate_manifest(repo: Path) -> dict[str, Any]:
    schema_names = {
        "agent": "agent-schema-v2.json",
        "metadata": "metadata-schema.json",
        "tool": "tool-definition-schema.json",
        "kit": "starter-kit-schema.json",
    }
    common = load_schema(repo, "common-schema.json")
    registry = Registry().with_resource(
        "common-schema.json", Resource.from_contents(common)
    )
    schemas = {key: load_schema(repo, value) for key, value in schema_names.items()}
    errors: list[dict[str, Any]] = []
    agents: list[dict[str, Any]] = []

    for folder in sorted((repo / "agents").iterdir()):
        if not folder.is_dir():
            continue
        relative = folder.relative_to(repo).as_posix()
        documents: dict[str, dict[str, Any]] = {}
        files = {
            "agent": folder / "agent.yaml",
            "metadata": folder / "metadata.yaml",
        }
        for kind, path in files.items():
            try:
                documents[kind] = load_yaml_mapping(path)
                messages = schema_messages(documents[kind], schemas[kind], registry)
            except (OSError, ValueError, yaml.YAMLError) as error:
                messages = [str(error)]
            if messages:
                errors.append({"path": path.relative_to(repo).as_posix(), "errors": messages})

        tool_records: list[dict[str, Any]] = []
        tools_root = folder / "tools"
        if tools_root.is_dir():
            for tool_folder in sorted(path for path in tools_root.iterdir() if path.is_dir()):
                tool_path = tool_folder / "tool.yaml"
                messages: list[str] = []
                image = None
                if tool_path.is_file():
                    try:
                        tool = load_yaml_mapping(tool_path)
                        messages = schema_messages(tool, schemas["tool"], registry)
                        infra = tool.get("infra") or []
                        if infra and isinstance(infra[0], dict):
                            image = (infra[0].get("image") or {}).get("acr")
                    except (OSError, ValueError, yaml.YAMLError) as error:
                        messages = [str(error)]
                else:
                    messages = ["missing tool.yaml"]
                if messages:
                    errors.append({"path": tool_path.relative_to(repo).as_posix(), "errors": messages})
                tool_records.append({
                    "name": tool_folder.name,
                    "image": image,
                    "has_dockerfile": (tool_folder / "Dockerfile").is_file(),
                    "schema_valid": not messages,
                })

        agent = documents.get("agent", {})
        metadata = documents.get("metadata", {})
        prompts = agent_yaml_prompts(agent) or usage_prompts(folder / "README.md")
        prompt_source = (
            "agent.yaml" if agent_yaml_prompts(agent)
            else "README Usage" if prompts
            else "generated fallback"
        )
        if not prompts:
            prompts = [fallback_prompt(folder.name, [tool["name"] for tool in tool_records])]
        agents.append({
            "name": folder.name,
            "display_name": agent.get("displayName") or agent.get("name") or folder.name,
            "path": relative,
            "metadata_tags": metadata.get("tags") or [],
            "associated_tools": metadata.get("associated_tools") or [],
            "tools": tool_records,
            "prompt_source": prompt_source,
            "candidate": prompts[0],
            "documented_usage_cases": prompts,
            "requires_manual_review": prompt_source == "generated fallback",
        })

    kits: list[dict[str, Any]] = []
    kits_root = repo / "starter-kits"
    for folder in sorted(path for path in kits_root.iterdir() if path.is_dir()):
        path = folder / "kit.json"
        try:
            kit = load_json(path)
            messages = schema_messages(kit, schemas["kit"], registry)
        except (OSError, json.JSONDecodeError) as error:
            kit = {}
            messages = [str(error)]
        if messages:
            errors.append({"path": path.relative_to(repo).as_posix(), "errors": messages})
        prompts = kit.get("samplePrompts") or []
        kits.append({
            "name": kit.get("name") or folder.name,
            "path": folder.relative_to(repo).as_posix(),
            "lifecycle": kit.get("lifecycle"),
            "agent_refs": kit.get("agentRefs") or [],
            "candidate": prompts[0] if prompts else None,
            "sample_prompts": prompts,
            "schema_valid": not messages,
        })

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "manifest-only; no deployment or invocation",
        "summary": {
            "agents": len(agents),
            "starter_kits": len(kits),
            "tool_instances": sum(len(agent["tools"]) for agent in agents),
            "readme_or_authored_prompt_agents": sum(
                agent["prompt_source"] != "generated fallback" for agent in agents
            ),
            "manual_review_agents": sum(agent["requires_manual_review"] for agent in agents),
            "schema_error_files": len(errors),
        },
        "schema_errors": errors,
        "agents": agents,
        "starter_kits": kits,
    }


def render_markdown(manifest: dict[str, Any]) -> str:
    summary = manifest["summary"]
    lines = [
        "# Catalog smoke manifest",
        "",
        f"Generated: {manifest['generated_at']}",
        "",
        "This dry run performs no Azure deployment, invocation, image build, push, or deletion.",
        "",
        "## Inventory",
        "",
        f"- Agents: **{summary['agents']}**",
        f"- Starter kits: **{summary['starter_kits']}**",
        f"- Tool instances: **{summary['tool_instances']}**",
        f"- Agents with authored/README prompts: **{summary['readme_or_authored_prompt_agents']}**",
        f"- Agents needing smoke-prompt review: **{summary['manual_review_agents']}**",
        f"- Files with schema errors: **{summary['schema_error_files']}**",
        "",
        "## Agent smoke candidates",
        "",
        "| Agent | Tags | Source | Category | Review? |",
        "|---|---|---|---|---|",
    ]
    for agent in manifest["agents"]:
        tags = ", ".join(agent["metadata_tags"])
        lines.append(
            f"| {agent['name']} | {tags} | {agent['prompt_source']} | "
            f"{agent['candidate']['category']} | "
            f"{'yes' if agent['requires_manual_review'] else 'no'} |"
        )
    lines.extend(["", "## Starter-kit smoke candidates", "", "| Kit | Agents | Prompt |", "|---|---:|---|"])
    for kit in manifest["starter_kits"]:
        title = (kit["candidate"] or {}).get("title", "missing")
        lines.append(f"| {kit['name']} | {len(kit['agent_refs'])} | {title} |")
    if manifest["schema_errors"]:
        lines.extend(["", "## Schema errors", ""])
        for item in manifest["schema_errors"]:
            lines.append(f"- **{item['path']}**: {'; '.join(item['errors'])}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo_root.resolve()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest = generate_manifest(repo)
    (output / "smoke-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    (output / "smoke-manifest.md").write_text(render_markdown(manifest), encoding="utf-8")
    print(json.dumps(manifest["summary"], indent=2))
    return 1 if manifest["schema_errors"] else 0


if __name__ == "__main__":
    sys.exit(main())