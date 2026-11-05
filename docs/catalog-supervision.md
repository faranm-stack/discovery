# Approval and reconciliation supervision

Pending tool confirmation is a resumable execution state, not a failed agent.
The runner now saves an approval checkpoint promptly instead of waiting the
entire invocation timeout. It does not approve or reject requests. The existing
response/conversation IDs are retained and cannot be resubmitted by the queue.

For explicitly enabled independent continuation, the queue can perform a bounded
number of read-only reconciliation checks. Only a terminal classified result
with `remote_may_be_running` explicitly false releases a global/lane reservation.
Confirmed-but-running work, missing operation receipts, unknown results and
read failures retain capacity. A denied-access result stops new submissions.
Exhaustion returns remaining work as not run rather than waiting indefinitely.

The CLI exposes `--reconcile-checks` and `--reconcile-interval`, and still requires
`--continue-independent` to continue around unresolved work. This is not a new
authorization to invoke agents. No server cancellation or automatic retry is
performed. A user-approved request that finishes later can be reconciled in
place; stale approval markers are cleared only after verified response reads.

Up to sixteen linked operation receipts can be checked, including unexpected
tool links. This is an evidence-reading bound, not an enforceable tool-operation
budget. The agent may make multiple tool calls inside one response. Until the
service exposes an appropriate enforcement boundary, do not claim a hard
operation/spend cap based on a prompt or client timeout.

OIDC preflight now uses the same renewable SDK credential path as execution in
GitHub Actions. Required OIDC fields/host checks fail closed without a CLI
fallback. Local runs retain strict existing-CLI target validation. This code
does not create identity trust, permissions, a protected environment, private
storage or live activation settings. Production integration targets
`microsoft/discovery`; the fork remains development/PR infrastructure.

Offline regression at this checkpoint: 673 tests passed across queue, runner,
confirmation, OIDC, reports, kits, planning and automation security-policy suites.
Mocked service transitions are not proof of unattended scientific execution.