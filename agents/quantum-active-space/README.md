# Quantum Active-Space Check

## Overview

A chemist can catch an impossible electron count before handing a reduced model to a solver. The tool does not choose the orbitals or determine total spin. This is a fork development package, not a deployed service or hardware benchmark.

## Architecture

The prompt asks for the physical assumptions and permission. One confirmed `active_space` action receives a JSON request in a mounted file. The standalone CPU backend writes `result.json` and returns a bounded summary; neither the command line nor the runtime interprets user inputs as code.

## Prerequisites

For local checks, use Python 3.12 and NumPy 2.3.5. No cloud account, model weights, or GPU is needed for these tests. Discovery use additionally requires a reviewed image built from this tool directory, an actual registered tool and a selected chat model. The registry and model references are deployment substitutions, not evidence of published resources.

## Configuration

orbitals counts spatial orbitals (1 to 1000); electrons counts active electrons; twice_spin is the signed integer difference between alpha and beta populations. The action accepts one `request_json` string, up to 64 KiB of UTF-8. Supply a fresh output directory; an existing result is not overwritten.

## Usage

Ask: “Can four active electrons occupy four spatial orbitals at zero spin projection?” Review [the example request](tools/quantum-active-space/example.json) before approving a call. From the [tool folder](tools/quantum-active-space), run `python -B action.py --request example.json --output run-output` for the same data-plane operation. Exit zero plus `status: completed` is required. Interpret twice_spin as twice the spin projection, not the spin multiplicity. Give alpha and beta counts separately.

## Tools

[quantumActiveSpace](tools/quantum-active-space/tool.yaml) exposes only `active_space`. The [container recipe](tools/quantum-active-space/Dockerfile) pins a Python Linux base by digest and NumPy wheels by hash. The [numerical implementation](tools/quantum-active-space/reference.py) has no service dependency. For local tests run `python -B -m unittest -v test_action` from the tool folder.

## Known Limitations

Bookkeeping only: no orbital selection, integral generation or chemical suitability claim. The reference fixtures are not a model-led conversation or an independent scientific review. An image build, service binding, and a bounded agent-to-tool roundtrip must be verified before a service-readiness claim.

## Contributing

See [the catalog contribution guide](../../CONTRIBUTING.md). Include a labeled failing case and preserve negative cases in subsequent changes. Do not publish a release without a confirmed publisher contact and support channel.
