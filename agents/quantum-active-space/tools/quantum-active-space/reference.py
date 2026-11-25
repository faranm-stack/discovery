"""Bounded CPU reference for the active_space action."""

import json

import numpy as np

VERSION = "0.2.0"

def integer(value, label, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{label}: integer in [{low}, {high}] required")
    return value

def active_space(r):
    orbitals = integer(r["orbitals"], "orbitals", 1, 1000)
    electrons = integer(r["electrons"], "electrons", 0, 2 * orbitals)
    spin = integer(r["twice_spin"], "twice_spin", -electrons, electrons)
    if (electrons + spin) % 2:
        raise ValueError("electron/spin parity mismatch")
    alpha, beta = (electrons + spin) // 2, (electrons - spin) // 2
    if max(alpha, beta) > orbitals:
        raise ValueError("spin occupancy exceeds active orbitals")
    return {"alpha_electrons": alpha, "beta_electrons": beta,
            "spin_orbitals": 2 * orbitals, "chemical_suitability": "not_assessed"}

OPERATIONS = {'active_space': active_space}

CONTRACTS = {'agents': [{'operation': 'active_space', 'required': ['orbitals', 'electrons', 'twice_spin'], 'limits': 'Bookkeeping only: no orbital selection, integral generation or chemical suitability claim.'}], 'optional_fields': {'active_space': []}}

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
