"""Bounded CPU reference for the error_correction action."""

import json

import math

import numpy as np

VERSION = "0.2.0"

def scalar(value, label):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"{label}: finite real number required")
    return float(value)

def integer(value, label, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{label}: integer in [{low}, {high}] required")
    return value

def error_correction(r):
    d = integer(r["distance"], "distance", 1, 31)
    p = scalar(r["bit_flip_probability"], "bit_flip_probability")
    if d % 2 != 1 or not 0 <= p <= 1:
        raise ValueError("odd distance and probability in [0,1] required")
    logical = math.fsum(math.comb(d, k) * p ** k * (1 - p) ** (d - k)
                        for k in range(d // 2 + 1, d + 1))
    return {"logical_bit_error_probability": logical,
            "noise_model": "independent_bit_flips_per_block", "full_quantum_protection": False}

OPERATIONS = {'error_correction': error_correction}

CONTRACTS = {'agents': [{'operation': 'error_correction', 'required': ['distance', 'bit_flip_probability'], 'limits': 'Independent bit flips and perfect syndrome/majority readout; not full quantum protection or a surface-code threshold.'}], 'optional_fields': {'error_correction': []}}

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
