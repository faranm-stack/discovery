# Quantum Repetition-Code Baseline

## Overview

A decoder developer can distinguish a simulation or counting error from a change in the noise assumptions before comparing more elaborate codes. This is a fork development package, not a deployed service or hardware benchmark.

## Architecture

The prompt asks for the physical assumptions and permission. One confirmed `error_correction` action receives a JSON request in a mounted file. The standalone CPU backend writes `result.json` and returns a bounded summary; neither the command line nor the runtime interprets user inputs as code.

## Prerequisites

For local checks, use Python 3.12 and NumPy 2.3.5. No cloud account, model weights, or GPU is needed for these tests. Discovery use additionally requires a reviewed image built from this tool directory, an actual registered tool and a selected chat model. The registry and model references are deployment substitutions, not evidence of published resources.

## Configuration

distance is an odd integer from 1 to 31; bit_flip_probability is a per-bit probability in the closed interval from zero to one. The action accepts one `request_json` string, up to 64 KiB of UTF-8. Supply a fresh output directory; an existing result is not overwritten.

## Usage

Ask: “Under independent bit flips, how often does an ideal three-bit majority decoder fail?” Review [the example request](tools/quantum-error-correction/example.json) before approving a call. From the [tool folder](tools/quantum-error-correction), run `python -B action.py --request example.json --output run-output` for the same data-plane operation. Exit zero plus `status: completed` is required. Report logical bit-error probability per block under independent flips and perfect readout. This does not correct arbitrary phase errors or estimate a surface-code threshold.

## Tools

[quantumErrorCorrection](tools/quantum-error-correction/tool.yaml) exposes only `error_correction`. The [container recipe](tools/quantum-error-correction/Dockerfile) pins a Python Linux base by digest and NumPy wheels by hash. The [numerical implementation](tools/quantum-error-correction/reference.py) has no service dependency. For local tests run `python -B -m unittest -v test_action` from the tool folder.

## Known Limitations

Independent bit flips and perfect syndrome/majority readout; not full quantum protection or a surface-code threshold. The reference fixtures are not a model-led conversation or an independent scientific review. An image build, service binding, and a bounded agent-to-tool roundtrip must be verified before a service-readiness claim.

## Contributing

See [the catalog contribution guide](../../CONTRIBUTING.md). Include a labeled failing case and preserve negative cases in subsequent changes. Do not publish a release without a confirmed publisher contact and support channel.
