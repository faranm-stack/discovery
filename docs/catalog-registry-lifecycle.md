# Catalog test-image lifecycle

## Registry ownership and access

The registry owners' November discussion permits community-catalog container
testing in the examples registry, conditional on cleaning up temporary material
that does not reach the community repository. No parallel test pipeline was
reported by the owner. This is intent/ownership context, not proof of effective
Azure role assignments or permission to replace existing tags.

Registry visibility, pull, push/build, and delete are separate capabilities.
In particular, AcrPush does not establish delete permission. Registry scope,
network policy, identity, assignment conditions and the actual operation must
be verified before writes. Do not grant roles to make a test pass.

## Default execution path

Test an existing community image at its resolved immutable digest first. A
regression run does not need to rebuild every container. Keep image identity,
tool definition hash, source commit, task/fixture hashes and operation receipts
together. A mutable tag alone does not establish that two runs used the same
image.

For a code change requiring a build, prefer a dedicated test registry or an
explicitly approved test repository namespace. Never overwrite shared `latest`
or release tags. Record owner, source commit/PR, run ID, digest, creation time,
expiry and retention decision. Registry-resource tags are not a substitute for
per-image inventory; use an external manifest and appropriate OCI labels.

## Cleanup is a reviewed action, not an automatic side effect

1. Enumerate only artifacts recorded as created by this pipeline.
2. Exclude any digest still referenced by an active tool, release, approved
   community example, unresolved execution, or retained reproduction evidence.
3. Generate a dry-run deletion plan with exact repository/tag/digest, owner,
   references checked, expiry and reason. Missing reference information blocks
   deletion. Never delete all untagged images or use a broad repository wildcard.
4. Require the registry owner's approval and verified delete capability.
5. Execute only the approved plan, recording deletion receipts and failures.

The automation in this branch does not build, push, retag or delete images.
No cleanup has been performed merely because this document exists. Long-lived
noncommunity work should use an owner-managed registry with its own retention
policy rather than relying on workspace lifetime.