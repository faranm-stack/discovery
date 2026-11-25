"""Bounded CPU reference for the spectroscopy action."""

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

def spectroscopy(r):
    samples = vector(r["samples"], "samples")
    if len(samples) < 8:
        raise ValueError("at least eight samples required")
    dt = scalar(r["sample_interval_s"], "sample_interval_s")
    if dt <= 0:
        raise ValueError("positive sample_interval_s required")
    if np.all(samples == samples[0]):
        raise ValueError("constant signal has no non-DC peak")
    # Binary rescaling preserves the frequency bins. Subtract an actual sample
    # before averaging so a common DC offset does not swamp small variations.
    exponent = math.frexp(float(np.max(np.abs(samples))))[1]
    with np.errstate(under="ignore"):
        scaled = np.ldexp(samples, -exponent)
        shifted = scaled - scaled[0]
        magnitudes = np.abs(np.fft.rfft(shifted - np.mean(shifted)))[1:]
    if not np.any(magnitudes):
        raise ValueError("no numerically resolved non-DC peak")
    peak = int(np.argmax(magnitudes) + 1)
    frequencies = np.fft.rfftfreq(len(samples), dt)
    return {"dominant_bin_hz": float(frequencies[peak]), "bin_width_hz": 1 / (len(samples) * dt),
            "nyquist_hz": 1 / (2 * dt), "bin_width_is_confidence_interval": False}

OPERATIONS = {'spectroscopy': spectroscopy}

CONTRACTS = {'agents': [{'operation': 'spectroscopy', 'required': ['samples', 'sample_interval_s'], 'limits': 'Spectral bin estimate only; no Hamiltonian identification, linewidth fit, aliased-frequency resolution or experimental discovery claim.'}], 'optional_fields': {'spectroscopy': []}}

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
