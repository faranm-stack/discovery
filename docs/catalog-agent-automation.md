# Catalog agent automation — local repair, not an enabled pipeline

Scope: two workflow files and a new standalone adapter plus offline tests. The
sweep, runner, inventory-memory module, snapshot acquisition and preparation
files are unchanged. No jobs, publication, pushes, login, account switches,
federation or role changes are authorized by this repair.

## Schedule and approvals

[Catalog Smoke Sweep](../.github/workflows/catalog-smoke-sweep.yml) schedules
Monday and Thursday at 08:00 UTC. Every trigger first creates an **offline plan**.
Schedules remain plan-only unless both `CATALOG_SMOKE_LIVE_ENABLED=true` and
`CATALOG_SMOKE_SCHEDULE_LIVE_ENABLED=true` exist, private evidence is approved,
the plan succeeds, and the protected `catalog-smoke` environment permits entry.
Manual dispatch defaults to `mode=plan`, zero invocations, CPU lane only.
Repository-level variables are required for job-level conditions; environment
variables alone cannot make a skipped job run. Preserve required reviewers,
branch restrictions and all tool confirmations; never automatically Proceed.

Live requires explicit bounded values, not truthy fallbacks:

| Setting | Meaning |
|---|---|
| `CATALOG_SMOKE_ARTIFACTS_APPROVED=true` | Owner approves private artifact retention; repository must also be private |
| `CATALOG_SMOKE_INPUTS_APPROVED=true` | Owner approves staged fixture contents, receipts and licenses; required when fixtures exist |
| `CATALOG_SMOKE_EXPECTED_AGENT_COUNT` | Scheduled approved current inventory count: **121 at this checkpoint**; fail on growth/drift until reviewed |
| `CATALOG_SMOKE_SCHEDULE_MAX_INVOCATIONS` | Positive response cap no larger than current inventory (121 for an approved whole-catalog allowance) |
| `CATALOG_SMOKE_SCHEDULE_CONCURRENCY` | Explicit 1..4 global workers; runner's existing lane limits still apply |
| `CATALOG_SMOKE_SCHEDULE_ALLOW_LANES` | Explicit comma-separated approved lanes: `cpu`, `gpu_capable`, `external_or_platform`, `resource_review` |
| `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` | Approved existing identity/target, not permission to create roles |
| `DISCOVERY_WORKSPACE_API_URL`, `DISCOVERY_RESOURCE_GROUP`, `CATALOG_SMOKE_MODEL` | Explicit existing target and model; project remains `weekly-agent-svc-test` |
| Secret `CATALOG_INPUT_ASSETS_YAML` | Owner-staged `assets` mapping accepted by the existing sweep; never upload its staging file |

The client timeout is 1800 seconds, the live job limit 360 minutes. These are
waiting limits, **not remote cancellation or a dollar cap**. Scheduled runs do
not register missing agents. Manual `deploy_missing` is separately explicit and
still never authorizes overwrite, tool edits, image builds or role setup.
Unresolved work can hold lanes; incomplete coverage is reported, not retried.

## Source, input and auth sequence

The new [adapter](../.github/scripts/catalog_workflow_preflight.py) does not call
the fixed [snapshot utility](../.github/scripts/snapshot_current_catalog.py): that
utility is deliberately bound to the approved 64c8708 snapshot and missing-24
acquisition contract, not arbitrary future main.

1. Generate a local offline plan. No upstream or credential access in this phase.
2. Before authentication/live, reuse `require_current_inventory` to resolve
   public upstream main to one full SHA and compare **all agent directories**.
   A 98-agent checkout against 121 upstream fails, even if a historical ledger
   calls itself whole-catalog. Network/invalid/truncated inventory fails closed.
3. Fetch the complete recursive Git tree **at that SHA**, compare every local
   agent file's Git blob digest and exact file set, reject special/linked files,
   and ensure every definition is present. Names alone are insufficient. Local
   repair overlays currently block; prepared-source approval remains parent work.
   This is content parity, not publisher signature verification. Starter-kit
   content parity is not asserted by the agent gate.
4. Require the plan to contain exactly the current inventory, and require every
   fixture in either plan inputs or the task-input manifest to have a valid
   Discovery storage-asset ARM ID, plus explicit owner fixture-content/license
   approval. No fixture upload or synthesis occurs here.
5. Require explicit budget/lanes, private evidence approval and future CLI
   `--run-label` support. This branch includes tested run-label support with
   service-safe short names. Full run identities remain in the receipts.
6. Only then select authentication: in GitHub Actions, validate the complete
   OIDC configuration and use the existing renewable SDK credential factory;
   locally, check **existing** Azure CLI authentication, selected subscription
   and tenant without signing in or switching accounts. GET the actual project
   agents route (not the misleading project metadata route), and GET each required
   asset's ARM metadata. Preserve the workspace endpoint allowlist, reject redirects
   on these authenticated reads, and require matching asset subscription/IDs and
   successful provisioning. Always close the credential; never log tokens,
   the OIDC request URL, or response bodies. Record `github_oidc_read_verified`
   or `existing_cli_read_verified` only after all reads succeed; a failed recheck
   cannot retain a previous successful authentication receipt.
   Metadata success does **not** prove the expected files/bytes are in the asset;
   fixture owners must approve staging/content receipts and licensing beforehand.

The private input mapping is created outside the artifact directory. Expected
schema: a YAML mapping named `assets`, whose keys are exact fixture names/paths
and values are existing storage-asset ARM IDs. Folder assets may satisfy multiple
keys. All required mappings are checked, including agents in unapproved lanes;
partial-lane approval must not be presented as complete coverage.

### Direct SDK OIDC and remaining authentication blockers

An Azure login action or `az login` is **not required** for the GitHub Actions
path. Preflight and execution use the existing
[credential factory](../utilities/agent-evaluation/evaluators/azure_credential.py),
which creates an SDK `ClientAssertionCredential` with a callback that requests a
fresh GitHub OIDC assertion when needed, including on renewal or a new audience.
This repair does not edit that shared factory or exercise real token exchange.

With `GITHUB_ACTIONS=true`, preflight requires all of `AZURE_CLIENT_ID`,
`AZURE_TENANT_ID`, `ACTIONS_ID_TOKEN_REQUEST_URL`, and
`ACTIONS_ID_TOKEN_REQUEST_TOKEN` before calling the factory. Client and tenant
must be UUIDs. The request URL must use HTTPS and a narrowly validated hostname
under `actions.githubusercontent.com` (including `token.actions.githubusercontent.com`);
userinfo, lookalike domains, non-443 ports, malformed DNS labels, whitespace and
fragments are rejected. GitHub supplies the request URL/token to eligible jobs;
do not copy them into logs or evidence. Missing or invalid CI configuration fails
closed: **no Azure CLI or default-credential fallback**, no login attempt.
Local runs retain strict CLI subscription/tenant checks and do not use the factory.

Direct SDK support does **not** establish authorization or enable live execution.
The owner must still verify existing federated trust for the repository/protected
environment and token audience, approved identity/tenant/subscription values,
and permissions for the Discovery project and required ARM assets.
`id-token: write` alone grants neither federated trust nor Azure resource access.
Private evidence approval, protected-environment review, source/input gates and
bounded live opt-ins remain blockers. No permissions, trust or settings are changed.

## Run identity and failure artifacts

Full identity records ISO week, `Run1Monday` or `Run2Thursday`, GitHub run ID and
attempt (for example `2026-W45-Run1Monday-123-a2`). The future CLI receives a
deterministic ten-character hex `--run-label`, derived from that full identity;
different attempts generate distinct labels in the focused tests. The full
identity, not this collision-resistant short hash, is the evidence key. Future
CLI contract must accept arbitrary `[A-Za-z0-9-]{1,12}` labels, combine with each
agent within the service's 24-character session-name limit, and detect collisions.
No fallback to old unlabelled sessions is permitted.

The plan directory is runner-temp/catalog-plan; live is runner-temp/catalog-sweep.
Always-finalization steps run the existing
[grader CLI](../.github/scripts/build_agent_scorecard.py) with the actual tracked
repository as `--repo-root`, and the **parent of the sweep folder** as
`--evidence-root` (matching the grader's `*/live/<agent>/response.json` lookup).
They generate:

- Raw catalog ledger (or separate workflow fallback ledger for corrupt/plan-only
  output after a live interruption); existing raw evidence is preserved.
- Separate automation grading input, never a rewrite of the raw verdict ledger.
- JSON, Markdown and HTML scorecards under `report-card/`.
- `next-run-memory.json` and `next-run-handoff.md` at the sweep directory root.
- Inventory/source receipts and sanitized failure-type receipts where applicable.

If the live process crashes before its final report, checkpoints become uncertain
and submission counts are retained; they are not silently replaced by the prior
offline plan. Grader failure saves diagnostic JSON/Markdown/HTML scorecards and handoff, with
nonzero exit. `always()` cannot survive runner loss, dependency-install failure,
or force cancellation: these remain explicit evidence gaps, not completed runs.

Raw evidence uploads require a private repository plus approved evidence and
have unique run-ID/attempt artifact names. Sweep retention is 7 days; kit raw
evidence 14; regraded reports 90. No raw report is concatenated into Actions
summaries; summaries are status only. Public repositories never upload raw
evidence, scorecards, handoffs, or planner reports.

### Public standalone reports and offline publication validation

The [public exporter](../.github/scripts/catalog_public_report.py) runs with
`always()` after sweep finalization (or kit report generation), on both public
and private repositories. It creates exactly two public files under
`RUNNER_TEMP/catalog-public-report`: standalone HTML with local CSS and CSV.
Upload steps name those two files individually, never a directory or glob.
Download the artifact and open the HTML locally; browser Find searches the table.
No JavaScript, remote stylesheet, image, fetch, or external service is required.
Public artifacts retain unique run ID/attempt names (7 days sweep, 14 days kits).

The exporter treats all input as untrusted. It projects only bounded lowercase
agent/kit slugs, enumerated statuses, derived grades and validated resource
categories with locally generated labels. Counts are recomputed from validated
rows and checked against input totals. An optional full 40-hex checkout SHA is
validated separately; it is not evidence attestation. The public grade mapping
is **passed → A, failed → F, blocked → B, uncertain → I, not_run → N**. These
are status shorthand, **never author attribution**, failure streaks or eviction
recommendations; they do not change private grading policy. Plan-mode reports
cannot claim runtime passed/failed verdicts. Kit static and E2E rows/counts are
separate: static A is not an E2E pass, and the offline kit adapter accepts only
blocked/not-run E2E states with zero executed assertions.

No free-text reasons, prompts, contacts, author metadata, paths, responses,
session/response IDs, token usage, tenant/subscription values or endpoints are
exported. Unknown resource values become the fixed `unknown` category; supplied
labels and grades are ignored. HTML cells are escaped and allowed CSV values
cannot start formulas. Invalid/missing input yields a fixed **unavailable**
HTML/CSV pair and nonzero exit, not a fabricated zero-error success. Duplicate
identifiers, malformed counts, invalid enums and duplicate JSON keys fail closed.
Raw evidence is neither copied nor embedded; the exporter does not read other
evidence files. Syntax validation is not secret discovery: approved public
identifiers and counts must themselves be suitable for publication.

Both workflows additionally trigger on pushes **only** to
`automation/catalog-validation-*` branches, running offline tests/plans without
Azure login or agent execution. The sweep live condition remains limited to
explicitly gated dispatch/schedule events, never push; the kit workflow has no
live job. GitHub schedules execute from the default branch only, not from these
publication branches. Existing live switches, private evidence approval and
protected-environment gates remain unchanged; this patch enables no live run.
Dependency installation still uses the package index; the planner/exporter are
offline. The [public report tests](../.github/tests/test_catalog_public_report.py)
cover secret-bearing inputs, invalid schemas, report output and workflow upload
boundaries. `always()` cannot guarantee artifacts after runner loss or missing
checkout/Python; those remain evidence gaps.

## Attribution and report safety

[Agent Report Card](../.github/workflows/agent-report-card.yml) is default-off on
`workflow_run`. Automatic regrading requires additional explicit
`CATALOG_SCORECARD_AUTOMATION_ENABLED=true`, private evidence approval, same
repository/default-branch provenance and protected-environment approval. Manual
regrading checks source workflow, event, repository, branch and exact current run
attempt before downloading. It executes only trusted default-branch harness code,
never downloaded scripts or the triggering head. Evidence is data only.

The unchanged grader's reason-based classifications alone are not verified author
defects. The adapter grades nonpasses **I / unknown attribution**, retains raw
status/reason, resets failure streaks and requires human review. Prior report
cards are deliberately not imported as failure history. No eviction candidates
or removals are inferred by automation. Existing direct grader behavior is not
changed by this local adapter.

Owner history comes from a full, tracked checkout, not exported snapshots or the
evidence folder. Missing/shallow/untracked history, root imports, bulk definition
imports and bot origins remain **Unknown**. A single tracked definition-creation
commit is recorded as origin evidence, not an independently verified GitHub
identity; no login is guessed and no author relay is performed. Metadata contact
is not proof of original authorship.

There is **no automatic issue/email path** on `workflow_run`. A separate job may
create a private **status-only** issue only on manual dispatch with `notify=true`,
`CATALOG_SCORECARD_NOTIFICATIONS_APPROVED=true`, private evidence approval and
protected-environment approval. It has no evidence, author contacts, mentions or
assignees in the body. GitHub may email subscribers; that is why this is opt-in.
Only that isolated job has `issues: write`; no email API or author relay is used.

## Validation and outstanding activation work

Focused offline tests cover stale 98/121 inventory, pinned byte parity, upstream
outage/truncation, fixture maps, limits/opt-ins, labels/attempts, failure scorecards
and memory, real grader invocation, unknown owners and no false eviction, and
workflow notification/privacy guards. These do not prove cloud execution or
branch/environment protection settings.

Authentication tests mock credentials and HTTP responses, prohibit real network,
and cover Actions success without subprocesses, missing/invalid OIDC configuration
before factory use, denied project/asset reads without success receipts, credential
cleanup, secret-safe failures, and the unchanged local CLI target checks. Shared
factory wiring is checked with a mocked SDK constructor; real assertion renewal,
federated trust and resource authorization remain unverified.

Local validation receipt (2026-11-04): branch
`automation/catalog-validation-20261104`, base commit
`8ee511951feaa69f8602892715b26dd4a24ac158`, plus this uncommitted scoped patch.
Using the existing sibling discovery-harness `.venv-smoke/Scripts/python.exe`
with `-B -m pytest` and `-p no:cacheprovider`:

- Baseline preflight/public-report suites: **229 passed**.
- Updated preflight suite: **98 passed**; public-report suite: **190 passed**;
   **288 passed total**, no skips or failures.
- Diff scope: exactly the four permitted files. Parsed workflow comparison,
   excluding step display names, is identical to the base; source/input/redirect
   guards are AST-identical, and shared credential factory bytes are unchanged
   after line-ending normalization. `git diff --check` passes.
- No real network, credentials, login, live workloads, commits, pushes or
   GitHub/Azure setting changes. This is mocked component proof, not live proof.

Publication/settings blockers (owner/parent work):

1. Publish all required parent scripts/dependencies/fixtures with this reviewed
   patch; several currently exist only as local untracked files. No push here.
2. Run-label support is implemented and tested; verify actual Studio selection
   during the separately approved live pilot rather than assuming UI behavior.
3. Synchronize checked-out agents to latest pinned upstream and approve any
   overlays through a distinct content-verification contract; 98 is not 121.
4. Stage and verify fixture contents, connector/license prerequisites and input
   map; metadata existence alone is not a usable-input receipt.
5. Verify existing protected-environment OIDC federated trust and actual target
   permissions for direct SDK authentication, without granting new roles. No
   mandatory Azure login step is needed; mocked local tests do not prove live access.
6. Owner verifies private evidence/retention, protected environment reviewers,
   trusted default branch and repository variables, then explicitly decides
   whether to enable live, scheduled live, automated reports and notifications.

No pipeline is claimed enabled; no live run or GitHub setting was exercised.