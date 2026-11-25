# Quantum Pure-State Entanglement

## Overview

A scientist reviewing simulated state preparation can calculate what the amplitudes support without mistaking random measurement counts for evidence of device entanglement. This is a fork development package, not a deployed service or hardware benchmark.

## Architecture

The prompt asks for the physical assumptions and permission. One confirmed `entanglement` action receives a JSON request in a mounted file. The standalone CPU backend writes `result.json` and returns a bounded summary; neither the command line nor the runtime interprets user inputs as code.

## Prerequisites

For local checks, use Python 3.12 and NumPy 2.3.5. No cloud account, model weights, or GPU is needed for these tests. Discovery use additionally requires a reviewed image built from this tool directory, an actual registered tool and a selected chat model. The registry and model references are deployment substitutions, not evidence of published resources.

## Configuration

State is an object with four real entries and an optional four-entry imag vector, ordered 00, 01, 10, 11. Inputs must already have unit norm. The action accepts one `request_json` string, up to 64 KiB of UTF-8. Supply a fresh output directory; an existing result is not overwritten.

## Usage

Ask: “Is the supplied simulated two-qubit pure state a Bell pair or a product state?” Review [the example request](tools/quantum-entanglement/example.json) before approving a call. From the [tool folder](tools/quantum-entanglement), run `python -B action.py --request example.json --output run-output` for the same data-plane operation. Exit zero plus `status: completed` is required. Return marginal entropy in bits, concurrence and the first qubit's reduced state. Do not apply the pure-state interpretation to density matrices or measurement counts.

## Tools

[quantumEntanglement](tools/quantum-entanglement/tool.yaml) exposes only `entanglement`. The [container recipe](tools/quantum-entanglement/Dockerfile) pins a Python Linux base by digest and NumPy wheels by hash. The [numerical implementation](tools/quantum-entanglement/reference.py) has no service dependency. For local tests run `python -B -m unittest -v test_action` from the tool folder.

## Known Limitations

Two-qubit pure states only; marginal entropy is not an entanglement measure for arbitrary mixed states. The reference fixtures are not a model-led conversation or an independent scientific review. An image build, service binding, and a bounded agent-to-tool roundtrip must be verified before a service-readiness claim.

## Contributing

See [the catalog contribution guide](../../CONTRIBUTING.md). Include a labeled failing case and preserve negative cases in subsequent changes. Do not publish a release without a confirmed publisher contact and support channel.
