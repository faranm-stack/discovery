# Starter-kit validation: deterministic offline planning

This is a **separate pipeline from individual-agent testing**. It reads local
catalog data, validates kit membership/dependencies, and plans explicit kit E2E
contracts. It does not install a kit, invoke an agent/tool, run science, deploy,
authenticate, email, open issues, or change permissions. There is deliberately
no runtime switch, plugin loading, subprocess, network client, or agent-result
import. Supplying a contract is not permission to execute it.

## Current inspected catalog and schema

Source of truth: [kit schema](schemas/starter-kit-schema.json),
[common schema](schemas/common-schema.json), and local manifests, not a stale
generated registry or a fixed kit count. On 2026-11-03 these are:

| Kit | Member references | Sample prompts | Primary |
| --- | ---: | ---: | --- |
| [chemistry-and-materials](../starter-kits/chemistry-and-materials/kit.json) | 20 | 4 | aizynthfinder |
| [chip-synthesis-timing](../starter-kits/chip-synthesis-timing/kit.json) | 2 | 5 | rtl-yosys-syn |
| [life-sciences](../starter-kits/life-sciences/kit.json) | 20 | 4 | alphafold |
| [physics-cfd-sim](../starter-kits/physics-cfd-sim/kit.json) | 1 | 3 | openfoam |

43 member references are not 43 unique agents. Shared members remain in each
kit's membership; summary counts distinguish total references and distinct
members. This inventories the **local checkout**, not verified current upstream
coverage. Empty inventory, unreadable manifests, and directories missing a
manifest are failures, never silently skipped.

The kit schema has `additionalProperties: false`, `agentRefs`, `samplePrompts`,
and prose `expectedOutput`. It does **not** define E2E contracts, fixtures,
install receipts, or artifact validators. No scientific scenario is invented
from a sample prompt. Existing kit definitions and schemas are unchanged.

## Offline validation and report contract

Entry point: [planner](../.github/scripts/catalog_kit_validation.py).
Use `--repo-root` to select the checkout, `--format json` (default) or
`--format markdown`. Both print to stdout; the planner writes no files. The
workflow retains stdout as JSON and Markdown in its temporary artifact directory.
Local verification can inspect stdout without adding files to this repository.

Checks include:

- Draft 7 schema validation with locally registered common/kit schemas only.
  Missing, empty, corrupt schemas and unresolved references fail closed; no URL
  is fetched to resolve a schema.
- Kit name/directory equality, unique canonical member refs, exactly one primary
  (which must be required), valid role and boolean required flags.
- Every declared member resolves to readable agent/metadata mappings with the
  registry's identity requirement (`name` or `display_name`). All active-kit refs
  must resolve, including optional refs, matching existing catalog policy.
- Metadata `associated_tools` paths resolve to readable tool-definition mappings.
  Tool directory names preserve the schema's mixed-case/underscore spelling.
  Declared `associated_agents` must be explicit kit members so the installation
  plan covers the dependency closure; missing members are not auto-installed.
  Native tool bindings are inventoried; runtime availability is **Unknown**.
  These presence checks are not complete agent/tool schema, image, transitive
  package, entitlement, or deployment validation; existing validators remain
  separate. No import or execution of catalog agent/tool source occurs.
- Repo-contained paths, no traversal or symlinked input files, duplicate JSON/YAML
  keys rejected, stable sorted results, and SHA256 hashes of all consumed inputs
  plus the planner itself. No timestamp or absolute checkout path is embedded.
  The plan hash is SHA256 of canonical sorted, indented ASCII JSON plus a final
  newline, before adding `plan_sha256`. This fingerprints observed local bytes;
  it does not attest a clean checkout, upstream revision, or deployed version.

Each kit has **separate** `static` and `e2e` results. E2E can only be:

| State | Meaning in this implementation |
| --- | --- |
| `blocked` | Static/dependency error, archived kit, missing/invalid E2E contract. Reasons are explicit. |
| `not_run` | Valid contract and static checks, but no protected runtime adapter supplied. |

`e2e.executed` is always false, `assertions_evaluated` is zero, and E2E passed,
failed, and executed counts are zero. Static `passed` is **never** kit E2E
`passed`. Individual-agent results cannot upgrade a kit. No previous pass is
carried forward. Invalid top-level contract documents block all contract checks;
unknown kit keys are configuration failures rather than silently ignored tests.

Exit 0 means the **offline planner/static checks** succeeded; missing contracts
still appear as blocked. Exit 1 means static/configuration/contract errors. A green
workflow means neither that any kit ran nor that kit E2E coverage is complete.
Summary counts explicitly expose blocked/not-run/zero execution.

## Explicit E2E test contracts (planning input only)

An optional `--contracts` argument selects a **repo-relative** JSON input. This
change adds no contracts to kit manifests and authors no unapproved scientific
workloads. The strict input schema is `CONTRACT_SCHEMA` in the planner. Shape:

- Top level: `schema_version: 1`, `kits` object keyed by exact kit directory name.
- Per kit: exact `manifest_sha256`, stable `scenario_id`, a declared `prompt_id`,
  `fixtures`, `install`, `handoffs`, and `artifacts`. New contracts should also
  declare **both** `mode` and `participants` as described below. Unknown fields
  fail schema validation; arbitrary commands, evaluator scripts, and adapter
  names are not accepted.
- `mode`: exactly `single_agent` or `multi_agent`. `participants`: a nonempty,
  duplicate-free array of exact canonical kit member refs (no aliases, case
  folding, whitespace trimming, or `input`/`output` pseudo-members). These are
  **scenario participants, not installation members**. Supplying only one of
  the two fields is invalid; neither value is silently inferred for an explicit
  scenario. Declared participants must equal the set of members appearing as
  handoff sources/targets; unused declarations and undeclared participants fail.
  - `single_agent`: exactly one participant, primary **or supporting**, chosen
    by the owner to match the selected sample prompt. No cross-agent handoff is
    required or fabricated, even when the kit contains many required members.
  - `multi_agent`: at least two participants and at least one provenance-valid
    transfer between **distinct declared member participants**. Independent
    deliveries to `output`, self-handoffs, or a transfer with a mismatched
    producer do not satisfy this requirement. It need not use the primary or
    every required installation member, and does not mean “run all agents.”
- Each fixture: `id`, repo-relative `path`, and pinned `sha256`. Every listed
  fixture must be readable, hash-matched, and used by a handoff. Empty fixture
  lists are allowed for scenarios with no file inputs; they do not prove that
  the sample prompt has all required scientific inputs.
- `install.agent_refs`: exactly the kit's declared members. `install.assertions`:
  exactly `installed_members_equal_declared` and `primary_launchable`. A future
  adapter must prove installation of the kit as a unit and a usable primary,
  not merely successful schema parsing or independent agent registration.
  This still includes **all** required and optional inventory members in both
  modes, even those not participating in the scenario. Primary launchability
  is an installation obligation, not a requirement to run the sample on it.
- Each artifact: `id`, declared participant `producer`, `media_type`, and assertions.
  Require both `exists` and `nonempty`, **plus a content assertion**: `sha256`
  (64 lowercase hex characters), `json_required_keys` (nonempty string array),
  or `text_contains` (nonblank string). Content assertions require `expected`;
  presence assertions must not carry it. Media type and content checks describe
  the future evaluator's obligations, not verified bytes in this offline report.
- Each handoff: unique `id`, `from`, `to`, `artifact_id`, and
  `assertion: same_artifact_sha256`. Producers must match the referenced output
  artifact, or `from: input` must reference a pinned fixture. Targets are declared
  participants or `output`; self-handoffs, outside members, and installed but
  nonparticipating members are invalid. **Both modes require a provenance-valid
  produced artifact delivery to `output`**. Forwarding an input fixture to output
  alone is insufficient. Unused fixtures/artifacts and duplicate IDs/checks
  remain errors. The handoff list is nonempty even for a single-agent scenario:
  output delivery is a handoff, but not a cross-agent one.

### Legacy v1 compatibility and migration

The document remains `schema_version: 1`. When **both** `mode` and `participants`
are absent, the original v1 rules remain: the primary and every required member
must appear in the handoffs, and a kit with more than one installed member needs
a cross-member handoff. A one-member kit only needs its produced output delivery.
Legacy optional members need not participate, but all installed members remain
eligible producers/endpoints. Previously valid contracts remain valid; invalid
provenance/endpoints now also cannot count toward output/cross-member obligations.
The report echoes the contract unchanged; absence of both fields identifies this
legacy policy. There is no automatic conversion or invented participant list.

To migrate, add both fields with the owner-selected scenario scope; leave the
installation list complete. For example, a two-member kit with primary
`agents/first` and supporting `agents/second` can use `mode: single_agent`,
`participants: ["agents/second"]`, an artifact produced by `agents/second`, and
one delivery from `agents/second` to `output`. Both members still appear in
`install.agent_refs`. A two-agent scenario instead declares both participants
and a producer-matched artifact transfer between them before output delivery.
The validator does not infer a target from the sample prompt's prose.

An owner must select a scientifically meaningful scenario, concrete fixtures,
and sufficiently discriminating content assertions. This structural validator
does not prove scenario feasibility, scientific accuracy, workflow reachability,
causal contribution of every participant, suitability of the selected agent for
the sample prompt, or that prose expected outputs have been fully covered.
“Provenance-valid” here means structurally consistent declarations, **not an
observed genuine runtime transfer**. A future adapter must
verify producer/consumer correlation and actual byte continuity, not trust an
assistant's claim or an artifact URI alone. One scenario's eventual pass would
prove that scenario only, not every kit prompt/member's functionality.

## Monday/Thursday and manual dry-run workflow

[Workflow](../.github/workflows/catalog-kit-validation.yml) declares Monday and
Thursday at 06:00 UTC (`0 6 * * 1,4`) and manual `workflow_dispatch`, both **offline
only**. It uses `contents: read`, no persisted checkout credentials, no OIDC,
no secrets, no protected environment, bounded runtime/concurrency, and no issue,
email, author notification, deployment or Discovery integration. Validator
dependency bootstrap uses PyPI; the actual planner/tests need no service access.
GitHub summary and 14-day artifacts retain only the offline plan/report. Catalog
and contract inputs must remain sanitized; do not place secrets in them.

**Local scheduled YAML is not an enabled schedule.** No workflow was published,
dispatched, committed or pushed by this implementation. Publication on the
default branch, Actions policy/enablement, schedule ownership, and an observed
scheduled run still need separate approval and verification. GitHub schedules
are best effort, not an exact-time SLA.

The user wants **agent AND kit tests twice per week**. This scoped change adds
only the kit offline schedule. It does not enable, modify, or assert completion
of the existing agent schedule. Neither suite's live cadence is proved here.

## Remaining protected-runtime integration gates

1. Owner-review a pinned meaningful contract for every kit; currently none of
   the four inspected manifests supplies one. Preserve all inventory rows.
2. Explicitly supply and approve an **existing protected runtime adapter** and
   its evidence schema before implementing any runtime hook. This version
   intentionally accepts no adapter argument and cannot execute one.
3. Keep authorization/permissions, protected environment, human confirmations,
   fixture installation, cleanup/rollback, measured usage, bounded concurrency,
   time and budget gates under existing controls. A client timeout is not remote
   cancellation. No automatic retries or new privileges are authorized.
4. Require correlated, revision-bound kit install receipts, primary launch
   evidence, producer/consumer handoff IDs and hashes, and verified output bytes
   for every asserted stage. Bind evidence to the exact plan, scenario and run;
   reject missing/stale/uncorrelated evidence and retain failed/blocked/not-run
   states. Agent success alone never satisfies these conditions.
5. Integrate separate agent and kit evidence under an agreed Monday/Thursday
   cadence without conflating counters. Publish/enable only after review; then
   observe real scheduled runs before claiming a live schedule or E2E coverage.

## Production-pattern assessment and verification

For this scoped kit pipeline: tool use is **present** as narrow local readers
and typed validation boundaries (no side-effecting runtime tool); reflection is
**present** as a deterministic evaluator with explicit assertions, no LLM/retry
loop; planning is **present** as hash-pinned JSON; runtime orchestration is
**missing by design**, not disguised as per-agent orchestration; memory/evidence
is **present** as input/planner hashes and bounded workflow artifact retention.
In-context: current definitions/schema only. Summarized: counts/reasons.
On disk: workflow temporary plan/report artifacts. Retrieved on demand: optional
contracts/fixtures. Retention: 14 days in Actions, none written by the CLI.

Smallest next change after this implementation: owner-reviewed contracts, not
an autonomous execution loop. It costs deterministic parsing and hashing only,
no model tokens or scientific compute; it prevents falsely promoting membership
checks into kit passes. Full runtime proof remains a separate approved step.

[Tests](../.github/tests/test_catalog_kit_validation.py) cover deterministic
replay/relocation, inventory completeness, schema/reference failures, optional
and required missing members, tool dependencies, malformed inputs, pinned
fixtures/contracts, install/handoff/artifact stage omissions, agent-pass
non-promotion, explicit single-agent supporting/primary delivery, exact scenario
participation, multi-agent provenance, legacy defaults, CLI exit/report semantics, workflow safety
and schedule, and the actual local kit inventory. Test passes are **local harness
unit tests**, not kit or agent runtime passes.

## Observed local verification — 2026-11-03

- Focused deterministic suite: **54 passed**, exit 0. Bytecode and pytest cache
  writes disabled; fixture data isolated in the operating system's temporary
  test directory. No dependency installation or service call was needed locally.
- Actual checkout offline planner: exit 0, four kits accounted for, 43 member
  references, 35 distinct members, four static passes, zero static failures.
- Kit E2E: **four blocked** (`e2e_contract_missing`), zero not-run with a valid
  contract, zero executed, zero passed, zero failed runtime tests. The synthetic
  unit-test contract demonstrates the separate `not_run` path, not live proof.
- JSON/Markdown are generated on stdout; no report files were added outside
  the four authorized source/documentation files. A future Actions run would
  retain its temporary reports as described above; no such run occurred here.
- Agent runtime tests, kit runtime tests, GitHub publication/dispatch/enablement,
  approvals, deployment, credentials, permissions and notifications were not
  exercised or changed. The actual integration gates above remain open.

## Observed scoped mode fix verification — 2026-11-04

- Edits restricted to the planner, its focused test module, and this document.
  No manifest, workflow, runtime adapter, or existing contract was changed.
- Baseline focused suite: **54 passed**. Updated focused suite: **95 passed**
  (41 additional regression cases), exit 0. Combined with the adjacent
  starter-kit validation and installation-audit fixture suites: **132 passed**,
  exit 0. These are local harness tests, not live agent/kit passes; audit
  transports are mocked or rejected before authentication.
- Contract schema self-validation and scoped trailing-whitespace checks passed;
  editor diagnostics reported no errors. Bytecode and pytest cache writes were
  disabled; test fixtures stayed in operating-system temporary directories.
- Current local checkout: four kits, 43 membership references, 35 distinct
  members, four static passes, no configuration errors. All four E2E cases
  remain blocked by missing contracts; zero executed and zero runtime passes.
- Explicit contracts can now target a single supporting member without
  fabricated coordination. Legacy contracts require migration of both fields
  to gain that behavior. This does not choose participants automatically,
  execute the sample, prove scientific suitability, or provide runtime evidence.
  No live calls, deployment, GitHub writes, or dependency installation occurred.