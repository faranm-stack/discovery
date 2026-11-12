# Preparing QuickStart downloads (maintainers)

Customers start with the scenario README and full owner guide. They do not
need this inventory or the packaging script to understand or run the scenario.

`downloads.json` records required filenames, byte lengths, SHA-256 hashes,
and publication status. It includes required inputs that are not yet publicly
available, so a clean checkout may report missing files. This inventory is
for packaging, not scientific execution. Source and published guide hashes are
recorded separately where they differ; archive bytes remain unchanged.

## Local verification and review download

Use an existing Python 3.10+ for these author-only operations; no package install,
network, extraction, agent invocation, model loading or scientific work occurs.

```console
python skills/quickstart-runner/scripts/package_download.py check --scenario biopharma
python skills/quickstart-runner/scripts/package_download.py prepare --scenario biopharma --destination ../QuickStart-BioPharma-review --local-review --apply
```

`prepare` is a no-write plan without `--apply`. It requires a new destination
and refuses unexpected files, modified bytes and symlinks. `--local-review`
means only local author review, not publication or science approval. It copies
exact inputs/guide into one new folder, adds the shared skill and short start
instructions, and creates a sibling ZIP containing the same bytes. ZIPs/tars
inside that outer download remain byte-identical to the originals.

The helper requires all listed source files and a completed guide before it
can prepare a bundle. Byte checks establish consistency, not permissions,
active skills, or runtime success.

## Public repository release

The BioPharma 168,396,900-byte snapshot exceeds GitHub's normal Git-object limit.
Use versioned repository releases for approved large files. The script creates
local bundles only; it does not upload them.

Before upload, verify rights, absence of private content, account/destination,
author approval and exact filenames/checksums. Preserve original source and
licence notices. The SiPhy archive is included in Git. BioPharma's exact snapshot
and checksum are now hosted in a [contributor-fork QuickStart prerelease](https://github.com/faranm-stack/discovery/releases/tag/QuickStart-BioPharma-frozen-reference-v1)
following explicit contributor public-sharing confirmation on 2026-11-12.
This is not an official Microsoft release or independent rights audit. The
protected CMS archive is not a public download. Local file availability
and checksum verification do not establish redistribution rights.

After an approved release is actually uploaded, put the **real download links**
in the scenario README's supporting-files section and the inventory, and verify
anonymous download bytes. Repository source-code ZIPs omit ignored files.

List unavailable reference outputs in the scenario README. Keep source guides
and scientific criteria intact; use the [blank template](../../../quickstarts/templates/QuickStart-Template.md)
when authoring a new scenario.

## Assistant-driven input download (Windows)

The BioPharma README is the customer entry point; no maintainer command is
required from the scientist. The receiving assistant resolves the supplied
folder URL to its actual repository and exact commit, reads the guide/runner,
and obtains approval to download the fixed reference inputs into a new folder.

[Get-QuickStartInputs.ps1](../scripts/Get-QuickStartInputs.ps1) runs on Windows
PowerShell 5.1+ without Python or third-party modules. Download and inspect the
script from the **same commit** used for its `-Revision` argument. Pass
`-Repository owner/repo`, `-Revision <40-character SHA>`, and a new short absolute
`-Destination`; add `-ApproveDownload` only after download consent. Without the
switch it performs no network or writes. It reads the pinned inventory, refuses
unapproved publication, validates seven original files and writes a downloaded-only
receipt. The shared skill and licence come from that same commit with recorded
hashes; no branch-latest fallback or alternate mirror is used.

The script does not extract archives, install Python/packages, invoke agents or
execute science. Existing destinations are refused and failed partials retained.
Public direct HTTPS is required; proxy-dependent networks and non-Windows
customer routes are unvalidated. Native setup and computation keep their own
approvals, exact runtime and acceptance criteria.

If hosting moves upstream, first obtain maintainer approval, upload the unchanged
snapshot under an agreed versioned release, verify an anonymous download against
the existing hash, then update the real URL together in the scenario README,
guide addendum and inventory. Update the inventory's guide size/hash whenever
the addendum changes. Do not invent a future URL or replace an asset in place.