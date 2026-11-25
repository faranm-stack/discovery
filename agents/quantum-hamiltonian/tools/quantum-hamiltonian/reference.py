"""Bounded CPU reference for the hamiltonian action."""

import json

import math

from fractions import Fraction

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

def compare_paulis(r, other):
    keys = {'energy_unit', 'terms', 'left_order', 'right_order'}
    if not isinstance(other, dict) or set(other) != keys:
        raise ValueError(f'compare requires exactly {sorted(keys)}; use null for unknown order')
    if other['energy_unit'] != r['energy_unit']:
        raise ValueError('comparison requires identical stated units; no implicit conversion')
    n = r['n_qubits']
    hamiltonian(dict(n_qubits=n, energy_unit=other['energy_unit'], terms=other['terms']))
    left, right = other['left_order'], other['right_order']
    for order in (left, right):
        if order is not None and not (isinstance(order,list) and len(order)==n and
                all(isinstance(s,str) and s.strip()==s and 1<=len(s)<=64 for s in order) and len(set(order))==n):
            raise ValueError('order requires distinct nonempty physical wire labels from left to right')
    if left is None or right is None:
        return {'verdict':'underdetermined','reason':'physical wire order missing; do not infer it from a drawing'}
    if set(left)!=set(right):
        raise ValueError('both orders must name the same physical wires')
    def coefficients(terms, indices):
        values = {}
        for term in terms:
            word = ''.join(term['pauli'][i] for i in indices)
            values[word] = values.get(word, 0) + Fraction(term['coefficient'])
        return values
    a = coefficients(r['terms'], range(n))
    b = coefficients(other['terms'], [right.index(label) for label in left])
    for word in sorted(a.keys() | b.keys()):
        if a.get(word,0) != b.get(word,0):
            return {'verdict':'different','order':left,'witness':{'pauli':word,
                    'left_coefficient_exact':str(a.get(word,0)), 'right_coefficient_exact':str(b.get(word,0))}}
    return {'verdict':'equal','order':left}

def inspect_hamiltonian(r):
    h = hamiltonian(r)
    comparison = {'comparison':compare_paulis(r,r['compare'])} if 'compare' in r else {}
    return {**comparison, "matrix": complex_json(h), "dimension": len(h),
            "trace": float(np.trace(h).real),
            "hermitian_residual": float(np.linalg.norm(h - h.conj().T))}

OPERATIONS = {'hamiltonian': inspect_hamiltonian}

CONTRACTS = {'agents': [{'operation': 'hamiltonian', 'required': ['n_qubits', 'terms', 'energy_unit'], 'limits': 'At most six qubits; real coefficients; no molecular integrals or inferred unit conversions.'}], 'optional_fields': {'hamiltonian': ['compare']}}

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
