"""Bounded CPU reference for the noise action."""

import json

import math

import numpy as np

VERSION = "0.2.0"

def scalar(value, label):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"{label}: finite real number required")
    return float(value)

def vector(value, label, maximum=4096):
    if not isinstance(value, list) or not 1 <= len(value) <= maximum:
        raise ValueError(f"{label}: nonempty bounded list required")
    return np.array([scalar(x, label) for x in value], dtype=float)

def times(request):
    t = vector(request["times_s"], "times_s", 256)
    if np.any(t < 0) or np.any(np.diff(t) <= 0):
        raise ValueError("times_s: nonnegative, strictly increasing required")
    return t

def noise(r):
    t1 = scalar(r["t1_s"], "t1_s")
    t2 = scalar(r["t2_s"], "t2_s")
    if t1 <= 0 or t2 <= 0 or t2 > 2 * t1:
        raise ValueError("require T1>0, T2>0 and T2<=2*T1")
    t = times(r)
    # A positive lifetime and nonnegative time imply a bounded exponential.
    # The model approaches zero; a zero in floating point is not invalid input.
    with np.errstate(over="ignore", under="ignore"):
        population, coherence = np.exp(-t / t1), np.exp(-t / t2)
    return {"pure_dephasing_rate_per_s": 1 / t2 - 1 / (2 * t1),
            "times_s": t.tolist(), "excited_population": population.tolist(),
            "normalized_coherence": coherence.tolist()}

OPERATIONS = {'noise': noise}

CONTRACTS = {'agents': [{'operation': 'noise', 'required': ['t1_s', 't2_s', 'times_s'], 'limits': 'Markovian relaxation plus pure dephasing with initially excited population and normalized coherence envelopes; not microscopic noise identification.'}], 'optional_fields': {'noise': []}}

def dispatch(request):
    """Return a bounded JSON envelope; invalid inputs cannot become success."""
    try:
        if not isinstance(request, dict) or request.get("operation") not in OPERATIONS:
            raise ValueError("known operation required")
        op = request["operation"]
        contract = next(c for c in CONTRACTS["agents"] if c["operation"] == op)
        allowed = {"operation", *contract["required"]}
        optional = set(CONTRACTS.get('optional_fields', {}).get(op, []))
        if not allowed <= set(request) or set(request) - allowed - optional:
            raise ValueError(f"exact fields required: {sorted(allowed)}")
        with np.errstate(all="raise"):
            result = OPERATIONS[op](request)
        envelope = {"status": "completed", "operation": op, "result": result,
                    "provenance": {"tool_version": VERSION, "numpy": np.__version__, "backend": "local_cpu",
                                   "basis_order": "leftmost Pauli is most significant qubit; lexicographic bitstrings",
                                   "energy_unit": request.get("energy_unit"), "limitations": contract["limits"]}}
        json.dumps(envelope, allow_nan=False)
        return envelope
    except (ValueError, KeyError, TypeError, OverflowError, FloatingPointError, np.linalg.LinAlgError) as exc:
        return {"status": "failed", "error": {"code": "INVALID_OR_UNSUPPORTED_INPUT", "message": str(exc)}}
