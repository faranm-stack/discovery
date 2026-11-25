"""Bounded CPU reference for the dynamics action."""

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

def vector(value, label, maximum=4096):
    if not isinstance(value, list) or not 1 <= len(value) <= maximum:
        raise ValueError(f"{label}: nonempty bounded list required")
    return np.array([scalar(x, label) for x in value], dtype=float)

def times(request):
    t = vector(request["times_s"], "times_s", 256)
    if np.any(t < 0) or np.any(np.diff(t) <= 0):
        raise ValueError("times_s: nonnegative, strictly increasing required")
    return t

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

def dynamics(r):
    if r["energy_unit"] != "rad/s":
        raise ValueError("dynamics requires H/hbar coefficients in rad/s")
    h = hamiltonian(r)
    psi = state(r["state"], len(h))
    t = times(r)
    eigenvalues, eigenvectors = np.linalg.eigh(h)
    weights = eigenvectors.conj().T @ psi
    evolution = (eigenvectors @ (np.exp(-1j * eigenvalues[:, None] * t) * weights[:, None])).T
    return {"times_s": t.tolist(), "probabilities": (np.abs(evolution) ** 2).tolist(),
            "max_norm_error": float(np.max(np.abs(np.sum(np.abs(evolution) ** 2, axis=1) - 1)))}

OPERATIONS = {'dynamics': dynamics}

CONTRACTS = {'agents': [{'operation': 'dynamics', 'required': ['n_qubits', 'terms', 'energy_unit', 'state', 'times_s'], 'limits': 'Closed-system unitary evolution only; rad/s convention H/hbar; no Lindblad or hardware execution.'}], 'optional_fields': {'dynamics': []}}

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
