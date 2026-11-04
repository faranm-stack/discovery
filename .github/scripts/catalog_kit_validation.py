#!/usr/bin/env python3
"""Deterministic, local-data-only kit planning. Never installs or invokes agents."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
import re
import sys
from typing import Any

from jsonschema import Draft7Validator, FormatChecker
from referencing import Registry, Resource
import yaml


AGENT_REF = r"^agents/[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$"
IDENTIFIER = {"type": "string", "pattern": r"^[a-z0-9][a-z0-9-]{0,63}$"}
SHA256 = {"type": "string", "pattern": "^[a-f0-9]{64}$"}


def _object(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "additionalProperties": False,
            "properties": properties, "required": required or list(properties)}


def _array(items: dict, minimum: int = 1) -> dict:
    return {"type": "array", "items": items, "minItems": minimum,
            "maxItems": 128, "uniqueItems": True}


# Contracts are separate test inputs: kit.json disallows additional properties.
# Assertion names describe FUTURE adapter obligations, not executed checks.
ARTIFACT = _object({
    "id": IDENTIFIER,
    "producer": {"type": "string", "pattern": AGENT_REF},
    "media_type": {"type": "string", "minLength": 3, "maxLength": 128},
    "assertions": _array(_object({
        "check": {"enum": ["exists", "nonempty", "sha256", "json_required_keys", "text_contains"]},
        "expected": {"type": ["string", "array"], "minLength": 1,
                     "minItems": 1, "items": {"type": "string", "minLength": 1}},
    }, ["check"])),
})
CONTRACT_SCHEMA = _object({
    "schema_version": {"const": 1},
    "kits": {"type": "object", "additionalProperties": _object({
        "manifest_sha256": SHA256,
        "scenario_id": IDENTIFIER,
        "prompt_id": {"type": "string", "minLength": 1, "maxLength": 64},
        "mode": {"enum": ["single_agent", "multi_agent"]},
        "participants": _array({"type": "string", "pattern": AGENT_REF}),
        "fixtures": _array(_object({
            "id": IDENTIFIER, "path": {"type": "string", "minLength": 1}, "sha256": SHA256,
        }), 0),
        "install": _object({
            "agent_refs": _array({"type": "string", "pattern": AGENT_REF}),
            "assertions": {"type": "array", "uniqueItems": True, "minItems": 2,
                           "maxItems": 2, "items": {"enum": ["installed_members_equal_declared", "primary_launchable"]}},
        }),
        "handoffs": _array(_object({
            "id": IDENTIFIER,
            "from": {"type": "string", "minLength": 1},
            "to": {"type": "string", "minLength": 1},
            "artifact_id": IDENTIFIER,
            "assertion": {"const": "same_artifact_sha256"},
        })),
        "artifacts": _array(ARTIFACT),
    }, ["manifest_sha256", "scenario_id", "prompt_id", "fixtures", "install", "handoffs", "artifacts"])},
})
# Both omitted preserves the original v1 contract; partial opt-in fails closed.
CONTRACT_SCHEMA["properties"]["kits"]["additionalProperties"]["dependencies"] = {
    "mode": ["participants"], "participants": ["mode"],
}


class InputError(ValueError):
    """Predictable invalid local input; no remote recovery or automatic retry."""


class _UniqueLoader(yaml.SafeLoader):
    """Do not allow duplicate YAML keys to hide a dependency declaration."""


def _yaml_mapping(loader: yaml.SafeLoader, node: yaml.MappingNode) -> dict:
    loader.flatten_mapping(node)
    pairs = loader.construct_pairs(node)
    try:
        return _unique_pairs(pairs)
    except (InputError, TypeError) as exc:
        raise InputError("duplicate_or_invalid_yaml_key") from exc


_UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _yaml_mapping)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=True) + "\n"


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise InputError("duplicate_json_key")
        result[key] = value
    return result


class Inputs:
    """Only regular, repo-contained files; evidence records byte hashes, not secrets."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.hashes: dict[str, str] = {}

    def read(self, relative: str) -> bytes:
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts or "\\" in relative or ":" in relative:
            raise InputError("unsafe_path")
        target = self.root / path
        if not target.resolve().is_relative_to(self.root):
            raise InputError("path_escapes_repository")
        if any(p.is_symlink() for p in (target, *target.parents) if p != self.root):
            raise InputError("symlink_input_not_allowed")
        try:
            data = target.read_bytes()
        except OSError as exc:
            raise InputError("missing_or_unreadable_file") from exc
        self.hashes[path.as_posix()] = _digest(data)
        return data

    def json(self, relative: str) -> Any:
        try:
            return json.loads(self.read(relative), object_pairs_hook=_unique_pairs)
        except (ValueError, UnicodeError) as exc:
            raise InputError("invalid_json") from exc

    def mapping(self, relative: str) -> dict:
        try:
            value = yaml.load(self.read(relative), Loader=_UniqueLoader)
        except (ValueError, UnicodeError, yaml.YAMLError) as exc:
            raise InputError("invalid_yaml") from exc
        if not isinstance(value, dict):
            raise InputError("yaml_mapping_required")
        return value


def _schema_errors(value: Any, schema: dict, registry: Registry | None = None) -> list[str]:
    """Do not echo input values or resolve remote references."""
    validator = Draft7Validator(schema, registry=registry or Registry(), format_checker=FormatChecker())
    return sorted({"schema:" + "/".join(map(str, e.absolute_path)) + ":" + str(e.validator)
                   for e in validator.iter_errors(value)})


def _validator(inputs: Inputs) -> tuple[dict | None, Registry, list[str]]:
    registry = Registry()  # No retrieval callback: unregistered refs fail closed.
    schemas = {}
    errors = []
    for name in ("common-schema.json", "starter-kit-schema.json"):
        try:
            schema = inputs.json("docs/schemas/" + name)
            if not isinstance(schema, dict) or not any(k in schema for k in ("type", "$ref", "allOf")):
                raise InputError("empty_schema")
            Draft7Validator.check_schema(schema)
            registry = registry.with_resource(name, Resource.from_contents(schema))
            schemas[name] = schema
        except Exception:
            # Schema/reference libraries have distinct exception hierarchies.
            # Fail closed without logging arbitrary schema values.
            errors.append("schema_configuration_invalid:" + name)
    return schemas.get("starter-kit-schema.json"), registry, errors


def _member(inputs: Inputs, entry: dict) -> dict:
    ref = entry["ref"]
    errors: list[str] = []
    tools = []
    native = []
    agent_dependencies = []
    try:
        metadata = inputs.mapping(ref + "/metadata.yaml")
        agent = inputs.mapping(ref + "/agent.yaml")
        if not (metadata.get("name") or metadata.get("display_name")):
            errors.append("registry_identity_missing")
        dependencies = metadata.get("associated_tools", [])
        if not isinstance(dependencies, list) or not all(isinstance(t, str) for t in dependencies):
            errors.append("tool_dependencies_invalid")
            dependencies = []
        if len(set(dependencies)) != len(dependencies):
            errors.append("tool_dependencies_duplicate")
        for tool in sorted(set(dependencies)):
            if not re.fullmatch(r"agents/[a-z0-9][a-z0-9-]*/tools/[a-zA-Z0-9](?:[a-zA-Z0-9_-]*[a-zA-Z0-9])?/?", tool):
                errors.append("tool_dependency_path_invalid")
                continue
            tool = tool.rstrip("/")
            tools.append(tool)
            try:
                inputs.mapping(tool + "/tool.yaml")
            except InputError as exc:
                errors.append("tool_dependency:" + tool + ":" + str(exc))
        associated = metadata.get("associated_agents", [])
        if not isinstance(associated, list) or not all(isinstance(a, str) and re.fullmatch(AGENT_REF, a) for a in associated):
            errors.append("agent_dependencies_invalid")
        else:
            agent_dependencies = sorted(set(associated))
            if len(agent_dependencies) != len(associated):
                errors.append("agent_dependencies_duplicate")
        # Native bindings are inventoried only; availability/entitlements Unknown.
        bindings = agent.get("tools", [])
        if not isinstance(bindings, list):
            errors.append("native_bindings_invalid")
        else:
            native = sorted({t if isinstance(t, str) else t.get("name", "")
                             for t in bindings if isinstance(t, (str, dict))
                             and isinstance(t if isinstance(t, str) else t.get("name", ""), str)})
    except InputError as exc:
        errors.append(str(exc))
    return {"ref": ref, "role": entry["role"], "required": entry["required"],
            "status": "failed" if errors else "passed", "errors": sorted(errors),
            "tool_dependencies": tools, "agent_dependencies": agent_dependencies, "native_bindings": native,
            "runtime_availability": "unknown"}


def _contract_errors(contract: dict, manifest: dict, manifest_hash: str, inputs: Inputs) -> list[str]:
    errors = []
    refs = {r["ref"] for r in manifest["agentRefs"]}
    required = {r["ref"] for r in manifest["agentRefs"] if r["required"]}
    primary = next(r["ref"] for r in manifest["agentRefs"] if r["role"] == "primary")
    # Installation inventory is not scenario participation. Only legacy v1
    # contracts infer requirements from kit size/roles; never rewrite their input.
    explicit = "mode" in contract
    mode = contract.get("mode", "multi_agent" if len(refs) > 1 else "single_agent")
    declared = set(contract["participants"]) if explicit else refs
    if not declared.issubset(refs):
        errors.append("contract_participant_not_member")
    if explicit and ((mode == "single_agent" and len(declared) != 1)
                     or (mode == "multi_agent" and len(declared) < 2)):
        errors.append("contract_participant_count_invalid")
    allowed = declared & refs
    if contract["manifest_sha256"] != manifest_hash:
        errors.append("contract_manifest_hash_mismatch")
    if contract["prompt_id"] not in {p["id"] for p in manifest.get("samplePrompts", [])}:
        errors.append("contract_prompt_not_declared")
    if set(contract["install"]["agent_refs"]) != refs:
        errors.append("contract_install_members_mismatch")
    fixtures = {f["id"]: f for f in contract["fixtures"]}
    artifacts = {a["id"]: a for a in contract["artifacts"]}
    if len(fixtures) != len(contract["fixtures"]) or len(artifacts) != len(contract["artifacts"]):
        errors.append("contract_duplicate_artifact_or_fixture_id")
    if set(fixtures) & set(artifacts):
        errors.append("contract_fixture_artifact_id_collision")
    handoff_ids = [h["id"] for h in contract["handoffs"]]
    if len(set(handoff_ids)) != len(handoff_ids):
        errors.append("contract_duplicate_handoff_id")
    for fixture in contract["fixtures"]:
        try:
            if _digest(inputs.read(fixture["path"])) != fixture["sha256"]:
                errors.append("contract_fixture_hash_mismatch:" + fixture["id"])
        except InputError as exc:
            errors.append("contract_fixture:" + fixture["id"] + ":" + str(exc))
    for artifact in contract["artifacts"]:
        checks = [a["check"] for a in artifact["assertions"]]
        if artifact["producer"] not in refs:
            errors.append("contract_artifact_producer_not_member")
        if explicit and artifact["producer"] not in declared:
            errors.append("contract_artifact_producer_not_participant")
        if not {"exists", "nonempty"}.issubset(checks) or not set(checks) & {"sha256", "json_required_keys", "text_contains"}:
            errors.append("contract_artifact_assertions_incomplete:" + artifact["id"])
        if len(set(checks)) != len(checks):
            errors.append("contract_duplicate_artifact_check:" + artifact["id"])
        for assertion in artifact["assertions"]:
            check, expected = assertion["check"], assertion.get("expected")
            valid = ((check in {"exists", "nonempty"} and expected is None)
                     or (check == "sha256" and isinstance(expected, str) and re.fullmatch(SHA256["pattern"], expected))
                     or (check == "text_contains" and isinstance(expected, str) and bool(expected.strip()))
                     or (check == "json_required_keys" and isinstance(expected, list) and bool(expected)))
            if not valid:
                errors.append("contract_assertion_expected_invalid:" + artifact["id"] + ":" + check)
    participants = set()
    consumed = set()
    final_output = False
    inter_agent = False
    for handoff in contract["handoffs"]:
        source, target, artifact_id = handoff["from"], handoff["to"], handoff["artifact_id"]
        participants.update({source, target} & refs)
        endpoints_valid = source != target and source in allowed | {"input"} and target in allowed | {"output"}
        if not endpoints_valid:
            errors.append("contract_handoff_endpoints_invalid:" + handoff["id"])
        producer = artifacts.get(artifact_id, {}).get("producer", "input" if artifact_id in fixtures else None)
        provenance_valid = producer == source
        if not provenance_valid:
            errors.append("contract_handoff_provenance_invalid:" + handoff["id"])
        consumed.add(artifact_id)
        if endpoints_valid and provenance_valid:
            final_output |= target == "output" and source in allowed and artifact_id in artifacts
            inter_agent |= source in allowed and target in allowed and source != target
    if explicit and participants != declared:
        errors.append("contract_participants_mismatch")
    if not explicit and (primary not in participants or not required.issubset(participants)):
        errors.append("contract_required_participants_missing")
    if not final_output:
        errors.append("contract_final_artifact_handoff_missing")
    if mode == "multi_agent" and not inter_agent:
        errors.append("contract_inter_agent_handoff_missing")
    if not (set(fixtures) | set(artifacts)).issubset(consumed):
        errors.append("contract_unreferenced_fixture_or_artifact")
    return sorted(set(errors))


def build_report(repo_root: Path, contracts_path: str | None = None) -> dict:
    """Account for every kit directory, including broken/missing manifests."""
    inputs = Inputs(repo_root)
    schema, registry, errors = _validator(inputs)
    contracts: dict = {}
    contract_invalid = False
    if contracts_path is not None:
        try:
            document = inputs.json(contracts_path)
            contract_issues = _schema_errors(document, CONTRACT_SCHEMA)
            if contract_issues:
                errors.extend("contracts:" + issue for issue in contract_issues)
                contract_invalid = True
            else:
                contracts = document["kits"]
        except InputError as exc:
            errors.append("contracts:" + str(exc))
            contract_invalid = True
    base = inputs.root / "starter-kits"
    kit_dirs = sorted(p for p in base.iterdir() if p.is_dir()) if base.is_dir() else []
    if not kit_dirs:
        errors.append("kit_inventory_empty_or_missing")
    names = {p.name for p in kit_dirs}
    for unknown in sorted(set(contracts) - names):
        errors.append("contract_unknown_kit:" + unknown)
    rows = []
    for directory in kit_dirs:
        kit = directory.name
        path = "starter-kits/" + kit + "/kit.json"
        issues = []
        members = []
        manifest: dict = {}
        try:
            data = inputs.json(path)
            if not isinstance(data, dict):
                raise InputError("manifest_object_required")
            manifest = data
            if schema is not None:
                try:
                    issues.extend(_schema_errors(manifest, schema, registry))
                except Exception:
                    issues.append("schema_reference_unresolvable")
            if manifest.get("name") != kit:
                issues.append("kit_name_directory_mismatch")
            refs = manifest.get("agentRefs")
            if not isinstance(refs, list) or not refs:
                issues.append("membership_missing")
                refs = []
            valid = [r for r in refs if isinstance(r, dict)
                     and isinstance(r.get("ref"), str) and re.fullmatch(AGENT_REF, r["ref"])
                     and isinstance(r.get("role"), str) and r["role"] in {"primary", "supporting"}
                     and isinstance(r.get("required"), bool)]
            if len(valid) != len(refs):
                issues.append("membership_entry_invalid")
            if len({r["ref"] for r in valid}) != len(valid):
                issues.append("membership_duplicate_ref")
            primaries = [r for r in valid if r["role"] == "primary"]
            if len(primaries) != 1 or not primaries[0]["required"]:
                issues.append("membership_primary_must_be_unique_and_required")
            members = [_member(inputs, r) for r in sorted(valid, key=lambda r: r["ref"])]
            member_refs = {m["ref"] for m in members}
            for member in members:
                # The kit installation contract cannot cover an undeclared
                # runtime agent dependency. Require explicit closure, not a
                # hidden deployment or an assumed external installation.
                for dependency in member["agent_dependencies"]:
                    if dependency not in member_refs:
                        member["errors"].append("agent_dependency_not_in_kit:" + dependency)
                        member["status"] = "failed"
            if any(m["errors"] for m in members):
                issues.append("membership_dependencies_invalid")
        except InputError as exc:
            issues.append(str(exc))
        if schema is None or any(e.startswith("schema_configuration_invalid:") for e in errors):
            issues.append("schema_configuration_invalid")
        contract = contracts.get(kit)
        contract_errors = []
        if contract is not None and not issues:
            contract_errors = _contract_errors(contract, manifest, inputs.hashes[path], inputs)
        reasons = []
        if issues:
            reasons.append("static_validation_failed")
        if manifest.get("lifecycle") == "archived":
            reasons.append("kit_archived")
        if contract_invalid:
            reasons.append("contracts_document_invalid")
        elif contract is None:
            reasons.append("e2e_contract_missing")
        reasons.extend(contract_errors)
        e2e = {"status": "blocked" if reasons else "not_run",
               "reasons": sorted(reasons) if reasons else ["protected_runtime_adapter_not_supplied"],
               "executed": False, "assertions_evaluated": 0,
               "contract_status": "invalid" if contract_errors or contract_invalid else (
                   "missing" if contract is None else "not_checked" if issues else "valid"),
               "contract": contract}
        rows.append({"kit": kit, "manifest": path, "manifest_sha256": inputs.hashes.get(path),
                     "lifecycle": manifest.get("lifecycle"),
                     "static": {"status": "failed" if issues else "passed", "errors": sorted(issues)},
                     "members": members,
                     "sample_prompt_ids": [p["id"] for p in manifest.get("samplePrompts", [])
                                           if isinstance(p, dict) and isinstance(p.get("id"), str)]
                     if isinstance(manifest.get("samplePrompts", []), list) else [],
                     "e2e": e2e})
    counts = Counter(row["e2e"]["status"] for row in rows)
    report = {"schema_version": 1, "mode": "offline", "runtime_enabled": False,
              "scope": "local_checkout_only_not_upstream_inventory_verified",
              "agent_tests": {"status": "not_run", "reason": "separate_individual_agent_pipeline"},
              "errors": sorted(errors), "kits": rows,
              "summary": {"kits": len(rows), "membership_references": sum(len(r["members"]) for r in rows),
                          "distinct_members": len({m["ref"] for r in rows for m in r["members"]}),
                          "static_passed": sum(r["static"]["status"] == "passed" for r in rows),
                          "static_failed": sum(r["static"]["status"] == "failed" for r in rows),
                          "e2e_passed": 0, "e2e_failed": 0, "e2e_blocked": counts["blocked"],
                          "e2e_not_run": counts["not_run"], "e2e_executed": 0},
              "input_sha256": dict(sorted(inputs.hashes.items())),
              "planner_sha256": _digest(Path(__file__).read_bytes())}
    report["plan_sha256"] = _digest(_json(report).encode("utf-8"))
    return report


def render_markdown(report: dict) -> str:
    def cell(value: Any) -> str:
        return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("|", "&#124;").replace("\n", " ").replace("\r", " ")

    lines = ["# Starter-kit offline validation", "",
             "Local planning/static checks only. No installation, handoff, artifact assertion, or agent test executed.",
             "Static membership success and individual agent passes are NOT kit E2E passes.",
             "Local scheduled YAML does NOT mean an enabled GitHub schedule.", "",
             "Plan SHA256: " + report["plan_sha256"], "",
             "| Kit | Static | Contract | Kit E2E | Reasons |", "| --- | --- | --- | --- | --- |"]
    for row in report["kits"]:
        reasons = row["static"]["errors"] + row["e2e"]["reasons"]
        lines.append("| " + " | ".join(map(cell, [row["kit"], row["static"]["status"],
                     row["e2e"]["contract_status"], row["e2e"]["status"], "; ".join(reasons)])) + " |")
    lines.extend(["", "## Dependency findings", ""])
    for row in report["kits"]:
        for member in row["members"]:
            if member["errors"]:
                lines.append("- " + cell(row["kit"]) + ": " + cell(member["ref"]) + " — " + cell("; ".join(member["errors"])))
    lines.extend(["", "## Counts", "", *[f"- {key}: {value}" for key, value in report["summary"].items()],
                  "", "## Configuration findings", "", *["- " + cell(e) for e in report["errors"]],
                  "", "## Remaining integration gates", "",
                  "- Owner-reviewed, manifest/fixture-pinned E2E contracts for every kit.",
                  "- Explicit existing protected runtime adapter, approvals, resource/time/budget limits and human confirmation handling.",
                  "- Correlated install receipts, handoff hashes, and verified output artifacts; never infer kit success from agent results.",
                  "- Separate individual-agent Monday/Thursday execution and reporting integration remains unverified.",
                  "- Publish/enable/review scheduled workflows separately; nothing here activates them.", ""])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--contracts", help="Optional repo-relative JSON contract input; never executed")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args(argv)
    report = build_report(args.repo_root, args.contracts)
    sys.stdout.write(_json(report) if args.format == "json" else render_markdown(report))
    # This is static/planning CI success, NEVER runtime success. Missing contracts
    # stay visible as blocked without turning every scheduled dry-run red.
    invalid_contract = any(r["e2e"]["contract_status"] == "invalid" for r in report["kits"])
    return int(bool(report["errors"] or report["summary"]["static_failed"] or invalid_contract))


if __name__ == "__main__":
    raise SystemExit(main())