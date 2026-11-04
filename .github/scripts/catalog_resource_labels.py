"""Report-only resource declarations; never change scheduler lanes or infer usage."""
import hashlib
import math
from pathlib import Path
import yaml

LABELS = {
    "gpu_required": "GPU required",
    "gpu_optional": "GPU optional",
    "no_gpu_declared": "No GPU declared",
    "external_platform": "External / platform",
    "unknown": "Needs resource review",
}


def declared_requirement(tools):
    if not tools:
        return "external_platform"
    bounds = []
    for tool in tools:
        infra = tool.get("infra") if isinstance(tool, dict) else None
        if not isinstance(infra, list) or not infra:
            return "unknown"
        for node in infra:
            compute = node.get("compute") if isinstance(node, dict) else None
            if not isinstance(compute, dict):
                return "unknown"
            low, high = compute.get("min_resources"), compute.get("max_resources")
            if not isinstance(low, dict) or not isinstance(high, dict):
                return "unknown"
            lo, hi = low.get("gpu"), high.get("gpu")
            if any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 or int(v) != v for v in (lo, hi)):
                return "unknown"
            if lo > hi:
                return "unknown"
            bounds.append((lo, hi))
    if any(lo > 0 for lo, _ in bounds):
        return "gpu_required"
    return "gpu_optional" if any(hi > 0 for _, hi in bounds) else "no_gpu_declared"


def resource_fields(repo, agent):
    folder = Path(repo) / "agents" / agent
    files = sorted((folder / "tools").glob("*/tool.yaml"))
    hashes = {p.relative_to(repo).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    try:
        category = declared_requirement([yaml.safe_load(p.read_text(encoding="utf-8")) for p in files])
        if not (folder / "agent.yaml").is_file():
            category = "unknown"
    except (OSError, yaml.YAMLError):
        category = "unknown"
    return {"resource_requirement": category, "resource_label": LABELS[category],
            "resource_definition_hashes": hashes, "observed_gpu_use": "not_assessed"}


RESOURCE_NOTE = ("Declared resources, not observed GPU use. GPU required: at least one tool infrastructure node "
    "declares min GPU > 0. GPU optional: min GPU = 0 and max GPU > 0; CPU suitability depends on the task. "
    "No GPU declared: explicit min/max GPU = 0. Missing or inconsistent bounds require review. "
    "External / platform: no local compute tool definition. Scheduling limits are separate and unchanged.")