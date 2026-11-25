"""Bounded CPU reference for the resources action."""

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

def resources(r):
    logical = integer(r["logical_qubits"], "logical_qubits", 1, 10 ** 9)
    ratio = integer(r["physical_per_logical"], "physical_per_logical", 1, 10 ** 9)
    depth = integer(r["logical_depth"], "logical_depth", 0, 10 ** 15)
    factory = integer(r["factory_qubits"], "factory_qubits", 0, 10 ** 15)
    cycle = scalar(r["cycle_time_s"], "cycle_time_s")
    if cycle <= 0:
        raise ValueError("positive cycle_time_s required")
    return {"physical_qubits": logical * ratio + factory,
            "assumed_serial_runtime_s": depth * cycle, "fault_tolerance_verified": False,
            "monetary_cost": None}

OPERATIONS = {'resources': resources}

CONTRACTS = {'agents': [{'operation': 'resources', 'required': ['logical_qubits', 'physical_per_logical', 'logical_depth', 'cycle_time_s', 'factory_qubits'], 'limits': 'Accounting, not a fault-tolerant QDK estimate: excludes routing, distillation throughput, memory/idle error and scheduling unless included in supplied assumptions. No pricing or advantage claim.'}], 'optional_fields': {'resources': []}}

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
