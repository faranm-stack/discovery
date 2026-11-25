"""Bounded CPU reference for the entanglement action."""

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

def state(value, size):
    if not isinstance(value, dict) or set(value) - {"real", "imag"}:
        raise ValueError("state: object with real and optional imag vectors required")
    real = vector(value["real"], "state.real", 64)
    imag = vector(value.get("imag", [0] * len(real)), "state.imag", 64)
    if real.size != size or imag.size != size:
        raise ValueError("state: dimension mismatch")
    psi = real + 1j * imag
    if not np.isclose(np.vdot(psi, psi).real, 1.0, atol=1e-10, rtol=0):
        raise ValueError("state: norm must be one; no implicit normalization")
    return psi

def complex_json(array):
    return {"real": array.real.tolist(), "imag": array.imag.tolist()}

def entanglement(r):
    psi = state(r["state"], 4)
    amplitudes = psi.reshape(2, 2)
    reduced = amplitudes @ amplitudes.conj().T
    spectrum = np.clip(np.linalg.eigvalsh(reduced), 0, 1)
    nonzero = spectrum[spectrum > 0]
    return {"reduced_state_A": complex_json(reduced),
            "entropy_bits": float(-np.sum(nonzero * np.log2(nonzero))),
            "concurrence": float(2 * abs(np.linalg.det(amplitudes)))}

OPERATIONS = {'entanglement': entanglement}

CONTRACTS = {'agents': [{'operation': 'entanglement', 'required': ['state'], 'limits': 'Two-qubit pure states only; marginal entropy is not an entanglement measure for arbitrary mixed states.'}], 'optional_fields': {'entanglement': []}}

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
