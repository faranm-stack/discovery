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
6. Only then check **existing** Azure CLI authentication, selected subscription
   and tenant, GET the actual project agents route (not the misleading project
   metadata route), and GET each required asset's ARM metadata. Require matching
   IDs and successful provisioning; never log tokens or response bodies.
   Metadata success does **not** prove the expected files/bytes are in the asset;
   fixture owners must approve staging/content receipts and licensing beforehand.

The private input mapping is created outside the artifact directory. Expected
schema: a YAML mapping named `assets`, whose keys are exact fixture names/paths
and values are existing storage-asset ARM IDs. Folder assets may satisfy multiple
keys. All required mappings are checked, including agents in unapproved lanes;
partial-lane approval must not be presented as complete coverage.

### Azure login publication blocker

Inspection found only a **floating** `azure/login@v2` in the utility evaluation
workflow, no existing SHA-pinned Azure login action. No hash was invented or
floating action copied. Consequently these workflows do **not** sign in. The
auth gate can reuse an existing authorized CLI context, but an ordinary fresh
hosted runner has none and must fail closed. Before live publication, the owner
must supply/review an authentic pinned login action and existing OIDC federation
for the protected environment. Add that reviewed login step after the source /
input gate and before the read-only auth check. `id-token: write` alone is not
authentication. No subscription/client/tenant values are guessed or roles granted.

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

Publication/settings blockers (owner/parent work):

1. Publish all required parent scripts/dependencies/fixtures with this reviewed
   patch; several currently exist only as local untracked files. No push here.
2. Run-label support is implemented and tested; verify actual Studio selection
   during the separately approved live pilot rather than assuming UI behavior.
3. Synchronize checked-out agents to latest pinned upstream and approve any
   overlays through a distinct content-verification contract; 98 is not 121.
4. Stage and verify fixture contents, connector/license prerequisites and input
   map; metadata existence alone is not a usable-input receipt.
5. Supply a genuine reviewed SHA-pinned Azure login and existing protected OIDC
   configuration. Verify actual target authorization without granting new roles.
6. Owner verifies private evidence/retention, protected environment reviewers,
   trusted default branch and repository variables, then explicitly decides
   whether to enable live, scheduled live, automated reports and notifications.

No pipeline is claimed enabled; no live run or GitHub setting was exercised.