"""Bounded CPU reference for the ground_state action."""

import json

import math

import numpy as np

VERSION = "0.2.0"

MAX_QUBITS = 6

UNITS = {"hartree", "eV", "rad/s", "dimensionless"}

PAULI = {
    "I": np.eye(2, dtype=complex),
    "X": np.array([[0, 1], [1, 0]], dtype=complex),
    "Y": np.array([[0, -1j], [1j, 0]], dtype=complex),
    "Z": np.diag([1, -1]).astype(complex),
}

def scalar(value, label):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"{label}: finite real number required")
    return float(value)

def integer(value, label, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{label}: integer in [{low}, {high}] required")
    return value

def complex_json(array):
    return {"real": array.real.tolist(), "imag": array.imag.tolist()}

def hamiltonian(request):
    n = integer(request["n_qubits"], "n_qubits", 1, MAX_QUBITS)
    if request["energy_unit"] not in UNITS:
        raise ValueError("energy_unit: unsupported; no implicit conversions")
    terms = request["terms"]
    if not isinstance(terms, list) or not 1 <= len(terms) <= 256:
        raise ValueError("terms: 1 to 256 Pauli terms required")
    matrix = np.zeros((2 ** n, 2 ** n), dtype=complex)
    for term in terms:
        if not isinstance(term, dict) or set(term) != {"pauli", "coefficient"}:
            raise ValueError("term: exactly pauli and coefficient required")
        word = term["pauli"]
        if not isinstance(word, str) or len(word) != n or set(word) - set(PAULI):
            raise ValueError("pauli: one I/X/Y/Z per qubit required")
        coefficient = scalar(term["coefficient"], "coefficient")
        product = np.array([[1]], dtype=complex)
        for letter in word:
            product = np.kron(product, PAULI[letter])
        matrix += coefficient * product
    return matrix

def eigen_result(matrix):
    values, states = np.linalg.eigh(matrix)
    ground = states[:, 0]
    # Numerical cluster width in the supplied energy unit, not a physical linewidth.
    tolerance = float(np.finfo(float).eps * len(values)) * float(np.max(np.abs(values)))
    return {
        "eigenvalues": values.tolist(), "ground_energy": float(values[0]),
        "ground_state": complex_json(ground),
        "ground_residual": float(np.linalg.norm(matrix @ ground - values[0] * ground)),
        "ground_degeneracy": int(np.count_nonzero(np.isclose(values, values[0], atol=tolerance, rtol=0))),
        "ground_degeneracy_tolerance": tolerance,
        "first_level_gap": float(values[1] - values[0]),
    }

def ground_state(r):
    return eigen_result(hamiltonian(r))

OPERATIONS = {'ground_state': ground_state}

CONTRACTS = {'agents': [{'operation': 'ground_state', 'required': ['n_qubits', 'terms', 'energy_unit'], 'limits': 'Exact only within the supplied finite model, not exact chemistry or quantum advantage.'}], 'optional_fields': {'ground_state': []}}

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
