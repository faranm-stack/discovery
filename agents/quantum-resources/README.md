# Quantum Resource Worksheet

## Overview

An architecture reviewer can separate a difference in arithmetic from a difference in assumptions without presenting a spreadsheet as a feasibility study. This is a fork development package, not a deployed service or hardware benchmark.

## Architecture

The prompt asks for the physical assumptions and permission. One confirmed `resources` action receives a JSON request in a mounted file. The standalone CPU backend writes `result.json` and returns a bounded summary; neither the command line nor the runtime interprets user inputs as code.

## Prerequisites

For local checks, use Python 3.12 and NumPy 2.3.5. No cloud account, model weights, or GPU is needed for these tests. Discovery use additionally requires a reviewed image built from this tool directory, an actual registered tool and a selected chat model. The registry and model references are deployment substitutions, not evidence of published resources.

## Configuration

logical_qubits and physical_per_logical are positive integers; factory_qubits and logical_depth are nonnegative integers. cycle_time_s is a positive duration in seconds. The action accepts one `request_json` string, up to 64 KiB of UTF-8. Supply a fresh output directory; an existing result is not overwritten.

## Usage

Ask: “What physical-qubit total and serial runtime follow from the explicitly stated overhead assumptions?” Review [the example request](tools/quantum-resources/example.json) before approving a call. From the [tool folder](tools/quantum-resources), run `python -B action.py --request example.json --output run-output` for the same data-plane operation. Exit zero plus `status: completed` is required. Separate data qubits from factory allocation and depth-times-cycle-duration from a schedule. Report the assumptions; do not imply fault tolerance, actual hardware capacity or a monetary estimate.

## Tools

[quantumResources](tools/quantum-resources/tool.yaml) exposes only `resources`. The [container recipe](tools/quantum-resources/Dockerfile) pins a Python Linux base by digest and NumPy wheels by hash. The [numerical implementation](tools/quantum-resources/reference.py) has no service dependency. For local tests run `python -B -m unittest -v test_action` from the tool folder.

## Known Limitations

Accounting, not a fault-tolerant QDK estimate: excludes routing, distillation throughput, memory/idle error and scheduling unless included in supplied assumptions. No pricing or advantage claim. The reference fixtures are not a model-led conversation or an independent scientific review. An image build, service binding, and a bounded agent-to-tool roundtrip must be verified before a service-readiness claim.

## Contributing

See [the catalog contribution guide](../../CONTRIBUTING.md). Include a labeled failing case and preserve negative cases in subsequent changes. Do not publish a release without a confirmed publisher contact and support channel.
