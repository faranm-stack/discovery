# Catalog pipeline publication checkpoint — November 4, 2026

## Available in this branch

- Agent offline validation and planning, plus separately gated live workflow code.
- Separate kit schema/member/contract planning; single-agent versus multi-agent
  scenarios are explicit. No kit runtime adapter or executed E2E proof is claimed.
- Standalone allowlisted HTML/CSV artifacts on the automation branch's push
  checks. Raw prompts, traces, resource IDs, contacts and error bodies are excluded.
- Monday/Thursday schedule declarations. GitHub schedules require default-branch
  placement and enablement; publication on a feature branch does not enable them.
- Immutable run labels, input/source gates, partial-usage accounting, resource
  requirement labels and durable failure-path report generation.

The public [historical HTML report](reports/catalog-2026-11-03/catalog-public-report.html)
and [CSV](reports/catalog-2026-11-03/catalog-public-report.csv) are sanitized exports
of local November 3 execution evidence, **not an Actions execution**. Download
the HTML file and open it in a browser; no login or companion files are needed
after download. GitHub's file viewer does not render arbitrary HTML as a page.
The report includes the 121-entry pinned baseline plus the explicitly labeled
legacy local-only sandboxaq entry (122 accounting rows); it does not claim 122
tested agents or silently discard the extra historical row.

## Inventory must not be hidden

Fork main at fabe96e148ed2efbfdabaca5d13e15c7f26ac04d contains 120 agent
directories. The pinned 121-entry baseline additionally contains bo-doe. This
automation-only branch intentionally does not import or alter scientific source
definitions. The public planning report adds baseline-only entries as not run
and reports the difference. Live execution still requires exact current upstream
inventory and byte parity; baseline accounting is not a substitute for that gate.

## Live activation blockers

1. Synchronize and review current agent/kit definitions, including bo-doe, without
   silently treating patched local definitions as upstream source.
2. Verify staged fixture bytes/licensing and complete the source-only scaffold
   tasks; known missing prerequisites remain explicit blocked results.
3. Configure an approved existing CI identity, authentic pinned Azure login,
   target permissions, protected environment and private raw-evidence destination.
   The present live job still requires a private repository; the public fork's
   sanitized reports do not remove that limitation. A reviewed external private
   evidence adapter is needed to run live from the public fork.
4. Implement kit runtime installation/scenario/handoff execution and assertions.
5. Add bounded automatic read-only reconciliation and supported operation-budget
   enforcement. Existing no-retry prompts alone cannot limit internal tool calls.
6. Adjudicate failure causes and original-author identity before sending notices
   or counting distinct scheduled failures toward eviction. Automatic notices
   remain disabled; no deletion or eviction is performed.

Pushing this branch runs only offline validation/planning. No merge, live
dispatch, live schedule activation, registry push/delete, role grant or email
notification is authorized by this publication.

## Validation evidence

The clean isolated checkout passed 546 focused offline tests, including the
repository automation security-policy suite. A subsequent 345-test subset passed
after environment-neutral defaults/test values were applied. These are local
test results, not GitHub Actions results; inspect the branch's Actions runs for
remote confirmation. No scientific workloads were launched by these tests.