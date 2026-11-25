# Quantum Hamiltonian Verifier

## Overview

A solver developer can compare two operators after explicit wire alignment instead of mistaking a display convention for a change in physics. This is a fork development package, not a deployed service or hardware benchmark.

## Architecture

The prompt asks for the physical assumptions and permission. One confirmed `hamiltonian` action receives a JSON request in a mounted file. The standalone CPU backend writes `result.json` and returns a bounded summary; neither the command line nor the runtime interprets user inputs as code.

## Prerequisites

For local checks, use Python 3.12 and NumPy 2.3.5. No cloud account, model weights, or GPU is needed for these tests. Discovery use additionally requires a reviewed image built from this tool directory, an actual registered tool and a selected chat model. The registry and model references are deployment substitutions, not evidence of published resources.

## Configuration

n_qubits is 1 to 6; terms contains 1 to 256 objects with a Pauli string and real coefficient; energy_unit is eV, hartree, dimensionless or rad/s. Optional compare requires terms, the same energy_unit, and left_order and right_order naming the same physical wires in string order (or null if unknown). The leftmost factor is most significant. The action accepts one `request_json` string, up to 64 KiB of UTF-8. Supply a fresh output directory; an existing result is not overwritten.

## Usage

Ask: “Does Pauli Z have zero trace, and which observable can distinguish two mislabeled qubits?” Review [the example request](tools/quantum-hamiltonian/example.json) before approving a call. From the [tool folder](tools/quantum-hamiltonian), run `python -B action.py --request example.json --output run-output` for the same data-plane operation. Exit zero plus `status: completed` is required. Report dimension, trace and Hermiticity residual. For comparison report the verdict and coefficient witness if present. A null wire order is underdetermined, not evidence of equality. An exact coefficient difference need not be physically significant.

## Tools

[quantumHamiltonian](tools/quantum-hamiltonian/tool.yaml) exposes only `hamiltonian`. The [container recipe](tools/quantum-hamiltonian/Dockerfile) pins a Python Linux base by digest and NumPy wheels by hash. The [numerical implementation](tools/quantum-hamiltonian/reference.py) has no service dependency. For local tests run `python -B -m unittest -v test_action` from the tool folder.

## Known Limitations

At most six qubits; real coefficients; no molecular integrals or inferred unit conversions. The reference fixtures are not a model-led conversation or an independent scientific review. An image build, service binding, and a bounded agent-to-tool roundtrip must be verified before a service-readiness claim.

## Contributing

See [the catalog contribution guide](../../CONTRIBUTING.md). Include a labeled failing case and preserve negative cases in subsequent changes. Do not publish a release without a confirmed publisher contact and support channel.

## Validating the fork draft

From this Discovery checkout, run `python -B agents/quantum-hamiltonian/dev/verify_family.py` to check all ten packages, or add `--only quantum-hamiltonian` for the verifier alone. The driver uses the checkout's catalog schema, structure and README checks and runs each package's tests in its own process. It requires the repository's validator dependencies and the pinned NumPy; it does not install anything.

For repeated development passes, add `--cache-dir` followed by a trusted directory outside the repository. An exact match on source, validator and local environment reuses a previous successful test receipt. Use `--fresh` for a fresh run. This checks new work on invocation; it does not schedule model runs, deploy images or certify a service.
