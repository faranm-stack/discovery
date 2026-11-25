"""Container entry point. The service binds request JSON as data, never as a command."""
import argparse
import json
from pathlib import Path

from reference import dispatch


def unique_fields(pairs):
    obj = {}
    for field, value in pairs:
        if field in obj:
            raise ValueError("duplicate JSON field")
        obj[field] = value
    return obj


def invalid_number(_):
    raise ValueError("nonfinite JSON number")


def failure(code, message):
    return {"status": "failed", "error": {"code": code, "message": message}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        with args.request.open("rb") as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise ValueError("request exceeds 64 KiB")
        request = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique_fields,
                             parse_constant=invalid_number)
        result = dispatch(request)
    except (OSError, ValueError, RecursionError) as exc:
        result = failure("INPUT_READ_ERROR", str(exc))

    try:
        args.output.mkdir(parents=True, exist_ok=True)
        with (args.output / "result.json").open("x", encoding="utf-8") as stream:
            json.dump(result, stream, allow_nan=False, separators=(",", ":"))
    except OSError as exc:
        result = failure("OUTPUT_WRITE_ERROR", str(exc))
    # Preserve full arrays in the artifact, rather than paying to put them in chat.
    summary = json.dumps(result, allow_nan=False)
    if len(summary) > 3000:
        brief = {"status": result["status"], "operation": result.get("operation"),
                 "artifact": "result.json", "inline_detail": "omitted"}
        detail = result.get("result", {})
        brief.update((k, v) for k, v in detail.items() if not isinstance(v, (list, dict)))
        comparison = detail.get("comparison")
        if comparison is not None:
            brief["comparison"] = {k: comparison[k] for k in ("verdict", "reason", "witness") if k in comparison}
        summary = json.dumps(brief, allow_nan=False)
        if len(summary) > 3000 and comparison is not None:
            # Exact rational witnesses can be lengthy. Never drop the decision.
            brief["comparison"] = {"verdict": comparison["verdict"], "detail": "see artifact"}
            summary = json.dumps(brief, allow_nan=False)
    print(summary)
    return 0 if result["status"] == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())