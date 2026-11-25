# Quantum Closed-System Dynamics

## Overview

A researcher can distinguish a wrong target or angular-frequency convention from a propagator error using labeled outcome probabilities over time. This is a fork development package, not a deployed service or hardware benchmark.

## Architecture

The prompt asks for the physical assumptions and permission. One confirmed `dynamics` action receives a JSON request in a mounted file. The standalone CPU backend writes `result.json` and returns a bounded summary; neither the command line nor the runtime interprets user inputs as code.

## Prerequisites

For local checks, use Python 3.12 and NumPy 2.3.5. No cloud account, model weights, or GPU is needed for these tests. Discovery use additionally requires a reviewed image built from this tool directory, an actual registered tool and a selected chat model. The registry and model references are deployment substitutions, not evidence of published resources.

## Configuration

Specify n_qubits, Pauli terms, energy_unit rad/s, a unit-norm state with real and optional imag arrays, and 1 to 256 nonnegative strictly increasing times_s. Basis bit strings are lexicographic. The action accepts one `request_json` string, up to 64 KiB of UTF-8. Supply a fresh output directory; an existing result is not overwritten.

## Usage

Ask: “Under a unit X generator, when does a qubit initially in zero invert?” Review [the example request](tools/quantum-dynamics/example.json) before approving a call. From the [tool folder](tools/quantum-dynamics), run `python -B action.py --request example.json --output run-output` for the same data-plane operation. Exit zero plus `status: completed` is required. Use a constant H/hbar in rad/s and times in seconds. Report labeled basis probabilities and the norm check. Neither a device measurement nor an open-system prediction follows.

## Tools

[quantumDynamics](tools/quantum-dynamics/tool.yaml) exposes only `dynamics`. The [container recipe](tools/quantum-dynamics/Dockerfile) pins a Python Linux base by digest and NumPy wheels by hash. The [numerical implementation](tools/quantum-dynamics/reference.py) has no service dependency. For local tests run `python -B -m unittest -v test_action` from the tool folder.

## Known Limitations

Closed-system unitary evolution only; rad/s convention H/hbar; no Lindblad or hardware execution. The reference fixtures are not a model-led conversation or an independent scientific review. An image build, service binding, and a bounded agent-to-tool roundtrip must be verified before a service-readiness claim.

## Contributing

See [the catalog contribution guide](../../CONTRIBUTING.md). Include a labeled failing case and preserve negative cases in subsequent changes. Do not publish a release without a confirmed publisher contact and support channel.
