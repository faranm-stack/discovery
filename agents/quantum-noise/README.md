# Quantum Relaxation Check

## Overview

A calibration scientist can decide whether a parameter pair is suitable for an idealized simulation or should be referred back to the person who measured it. This is a fork development package, not a deployed service or hardware benchmark.

## Architecture

The prompt asks for the physical assumptions and permission. One confirmed `noise` action receives a JSON request in a mounted file. The standalone CPU backend writes `result.json` and returns a bounded summary; neither the command line nor the runtime interprets user inputs as code.

## Prerequisites

For local checks, use Python 3.12 and NumPy 2.3.5. No cloud account, model weights, or GPU is needed for these tests. Discovery use additionally requires a reviewed image built from this tool directory, an actual registered tool and a selected chat model. The registry and model references are deployment substitutions, not evidence of published resources.

## Configuration

t1_s and t2_s are positive lifetimes in seconds, with t2_s at most twice t1_s. times_s contains 1 to 256 nonnegative, strictly increasing sample times. The action accepts one `request_json` string, up to 64 KiB of UTF-8. Supply a fresh output directory; an existing result is not overwritten.

## Usage

Ask: “Can the reported relaxation and coherence times describe the same Markovian decay model?” Review [the example request](tools/quantum-noise/example.json) before approving a call. From the [tool folder](tools/quantum-noise), run `python -B action.py --request example.json --output run-output` for the same data-plane operation. Exit zero plus `status: completed` is required. Report the pure-dephasing rate and the separate excited-population and normalized-coherence envelopes. A late-time rounded zero is not exact physical extinction. Reject an inconsistent pair without diagnosing a device fault.

## Tools

[quantumNoise](tools/quantum-noise/tool.yaml) exposes only `noise`. The [container recipe](tools/quantum-noise/Dockerfile) pins a Python Linux base by digest and NumPy wheels by hash. The [numerical implementation](tools/quantum-noise/reference.py) has no service dependency. For local tests run `python -B -m unittest -v test_action` from the tool folder.

## Known Limitations

Markovian relaxation plus pure dephasing with initially excited population and normalized coherence envelopes; not microscopic noise identification. The reference fixtures are not a model-led conversation or an independent scientific review. An image build, service binding, and a bounded agent-to-tool roundtrip must be verified before a service-readiness claim.

## Contributing

See [the catalog contribution guide](../../CONTRIBUTING.md). Include a labeled failing case and preserve negative cases in subsequent changes. Do not publish a release without a confirmed publisher contact and support channel.
