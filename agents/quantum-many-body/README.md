# Quantum Finite Spin Chain

## Overview

A many-body solver developer can test signs and open boundary conditions before investing in a larger simulation. This is a fork development package, not a deployed service or hardware benchmark.

## Architecture

The prompt asks for the physical assumptions and permission. One confirmed `many_body` action receives a JSON request in a mounted file. The standalone CPU backend writes `result.json` and returns a bounded summary; neither the command line nor the runtime interprets user inputs as code.

## Prerequisites

For local checks, use Python 3.12 and NumPy 2.3.5. No cloud account, model weights, or GPU is needed for these tests. Discovery use additionally requires a reviewed image built from this tool directory, an actual registered tool and a selected chat model. The registry and model references are deployment substitutions, not evidence of published resources.

## Configuration

n_qubits is 1 to 6; coupling and field are real coefficients in the explicitly supplied energy_unit. There is no wrap-around bond. The action accepts one `request_json` string, up to 64 KiB of UTF-8. Supply a fresh output directory; an existing result is not overwritten.

## Usage

Ask: “Which levels should a two-site open ferromagnetic chain have at zero transverse field?” Review [the example request](tools/quantum-many-body/example.json) before approving a call. From the [tool folder](tools/quantum-many-body), run `python -B action.py --request example.json --output run-output` for the same data-plane operation. Exit zero plus `status: completed` is required. Use the open transverse-field Ising Hamiltonian with negative coupling and field prefactors. Report finite-size levels and numerical degeneracy; do not extrapolate a bulk critical point.

## Tools

[quantumManyBody](tools/quantum-many-body/tool.yaml) exposes only `many_body`. The [container recipe](tools/quantum-many-body/Dockerfile) pins a Python Linux base by digest and NumPy wheels by hash. The [numerical implementation](tools/quantum-many-body/reference.py) has no service dependency. For local tests run `python -B -m unittest -v test_action` from the tool folder.

## Known Limitations

Open chain, at most six spins; no thermodynamic phase-transition or material-discovery claim. The reference fixtures are not a model-led conversation or an independent scientific review. An image build, service binding, and a bounded agent-to-tool roundtrip must be verified before a service-readiness claim.

## Contributing

See [the catalog contribution guide](../../CONTRIBUTING.md). Include a labeled failing case and preserve negative cases in subsequent changes. Do not publish a release without a confirmed publisher contact and support channel.
