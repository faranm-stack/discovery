# QuickStart BioPharma — frozen reference

Reproduce a frozen-reference lead-generation workflow with the
**[BioPharma v0.11 QuickStart guide](QuickStart-BioPharma%20LeadGen%20v0.11.md)**.

**Availability:** All six inputs have public download locations. The large
snapshot is hosted in a versioned **contributor-fork QuickStart prerelease**,
not an official Microsoft release. Its original bytes and licence notices are
retained. The updated repo-link launch still needs a fresh Stable execution test;
download verification is not a scientific pass.

## Start here — give Discovery this folder link

You do not need to clone the repository, install Git, download individual files
or unzip them yourself. The assistant prepares the inputs after your approval.

1. Open **Microsoft Discovery Stable**. Use the dropdown beside the current
	project name to return to the project selector, then click **New project**
	in the local-project view.
2. In the folder picker, navigate to **Downloads**, create a new empty folder
	named **QuickStart-BioPharma**, select it, and click **Open local project**.
	Use a new name if it already exists; do not reuse an old tested project.
3. Click **New session**. In the chat's model selector choose
	**GPT-6 Astra (`gpt-6-astra`)**. If it is unavailable, stop and resolve access.
4. Copy this scenario folder's URL from your browser. Paste it into the chat
	with the following message (replace the bracketed text with that URL):

> Run the BioPharma QuickStart from [paste this GitHub folder link]. Read its
> README, full guide and shared QuickStart runner. Help me prepare this new
> local project: download and verify the required inputs, then guide setup,
> execution and result checks. Ask before downloading/preparing files,
> installing anything or starting computation. Use the default frozen-reference
> workflow, not fresh discovery. Stop and explain any missing capability or
> mismatched version; do not reuse another project's files or results.

**No file attachments are needed for this link-based route** if the assistant
can read GitHub and download files with its local tools. If it cannot, it should
say what is missing, not claim to have loaded the guide or silently switch methods.
The optional manual route below is a separate fallback, not proof this route works.
Project labels were checked against Windows Stable 0.0.4; other builds may differ.

### Instructions for the receiving assistant

1. Resolve the user's GitHub folder URL to its **actual repository and exact
	40-character commit SHA**. Stay on that fork/branch; do not substitute upstream
	`main`. Read this README, the full guide and
	[shared runner](../../../skills/quickstart-runner/SKILL.md) at the same commit.
	Confirm the loaded revision and default scope in chat. Resolve branch names
	through GitHub metadata, including names containing slashes; do not guess.
2. Check local file/network/command access, model, OS/architecture, runtime,
	short destination and at least the guide's required free space. Display all
	14 dependencies and ask the guide's ordinary-chat Yes/No selection question
	even when Python is missing. This is not install or compute approval.
3. On Windows, propose a **new `QuickStart-inputs` subfolder** in the fresh
	project (short absolute local path, maximum 120 characters for the downloader).
	Request permission to download about 169 MB of public reference inputs and
	the small setup documents. Do not upload customer data or request GitHub
	credentials. Do not change the app's active project behind the user.
4. After download approval, obtain and inspect
	[Get-QuickStartInputs.ps1](../../../skills/quickstart-runner/scripts/Get-QuickStartInputs.ps1)
	from that **same exact commit**. Save it as a new file without overwriting
	anything. Invoke it with `-Repository` set to the URL's owner/repo,
	`-Revision` set to that commit, `-Destination` set to the new input folder,
	and `-ApproveDownload`. Without the switch it prints a plan only.
	It works with Windows PowerShell 5.1+; Python is not needed to download inputs.
	Do not bypass execution policy or TLS checks. Non-Windows acquisition with
	this helper is not supported or validated; report that boundary.
5. The helper uses the same-commit
	[download inventory](../../../skills/quickstart-runner/references/downloads.json),
	downloads the guide and six exact inputs, verifies their sizes/SHA-256 hashes,
	and fetches the shared runner and licence. Read the downloaded guide and
	`quickstart-runner.md` explicitly; a file on disk is not automatic activation.
	Record `QuickStart-download-receipt.json` as **downloaded-only**, not setup PASS.
6. Follow section 5 of the full guide in that input folder. Obtain separate
	preparation/extraction and installation approvals. Safely extract only the
	small operational ZIP after checking its manifest; leave the large snapshot
	zipped for the supplied launcher. Preserve the older bundled guide at its
	manifest path; the selected v0.11 guide governs. Verify preflight, then ask
	for the guide's separate bounded computation approval.

The downloader never extracts, installs or runs science. A failed transfer keeps
its partial files; report the error and ask before retrying into a different new
folder. It uses direct public HTTPS without proxy authentication; a network
requiring a proxy is not a validated route. Do not silently use cached inputs.

## Optional manual download route

Use this only if you deliberately choose manual setup instead of the link-based
route. It remains available for troubleshooting and local package tests.

### 1. Get the guide and its files

Obtain the guide and the **six required files** listed under
[Supporting files](#supporting-files). Keep their original filenames.

For files available on GitHub, open the file page and choose **Download raw
file**. Also download the [shared runner instructions](../../../skills/quickstart-runner/SKILL.md).
Saving the GitHub HTML page is not the same as downloading the file.

Create a new short folder, for example `QuickStart-BioPharma` in Downloads. Put the guide,
both unopened ZIPs, both checksum files, manifest CSV and validation JSON in it.
Do not copy old sessions or installed environments. The small ZIP includes
the launcher and historical validation files.

### 2. Create the project and load the runner

1. Open **Microsoft Discovery Stable**, not Preview. If another project is open,
	use the dropdown beside its name to return to the project selector.
2. In the local-project view, click **New project**. Navigate to **Downloads**,
	select your new **QuickStart-BioPharma** folder, and click **Open local project**.
	Select the folder containing the guide and inputs, not an individual ZIP.
	The folder picker may hide individual files; that is normal.
3. Confirm the displayed project path is your new folder. Initialize the local
	project if prompted, then click **New session** in the left sidebar.
4. In the chat's model selector, choose **GPT-6 Astra (`gpt-6-astra`)**. If it
	is unavailable, stop and resolve model access rather than choosing another model.
5. Use the chat input's attachment control to select files. Browse to your
	project folder, hold **Ctrl** and select these **two Markdown files**, then
	click **Attach files**:
	- The downloaded shared runner **SKILL.md** (named **quickstart-runner.md**
	  in locally prepared packages): the assistant's setup and execution instructions.
	- **QuickStart-BioPharma LeadGen v0.11.md**: the exact scientific procedure,
	  dependencies, prompt, and acceptance criteria.
6. Confirm both filenames appear as attachments, then send the instruction below.

These project and attachment labels were checked against Windows Stable 0.0.4.
If your build differs, record the screen rather than selecting a cloud setup.
No slash command is needed for this explicit-loading route. Keep the ZIPs and
integrity attachments in the project folder; do not attach them to chat.

> Use the quickstart-runner skill to follow the attached BioPharma v0.11
> QuickStart guide from these supplied files. Run the default frozen-reference
> scenario. Check readiness first, prepare the supplied bundle when approved,
> and keep dependency selection, installation and computation approvals separate.

Confirm the assistant can read both attachments before continuing. Neither
Markdown file is a Python program. Use the default frozen-run instructions in
**section 5**; section 6's fresh-discovery prompt is a different workflow.

## Follow setup in the guide

The assistant checks the model, OS/architecture, Python, files, space and access.
The reference needs **Python 3.13.14, 64-bit**. If absent, the guide supplies
the publisher installation steps; then the assistant rechecks the exact
interpreter. **Python is not installed automatically by the package or launcher.**
The guide directs you to the [Python 3.13.14 release](https://www.python.org/downloads/release/python-31314/)
and the **Windows installer (64-bit)** for Windows x64. Follow its per-user
installation steps, then ask the assistant to recheck.

Python 3.13.17 is not the pinned version. If an installer reports that a newer
version is already installed, stop and record the message. Do not uninstall or
downgrade it, change the pins, or reuse another project's environment.
An approved separate exact interpreter may be needed. **pip installs packages,
not Python itself.** After separate installation approval, the assistant uses
the supplied setup to install packages into a new isolated environment.

It shows **all 14 packages** from the guide and asks the full-set **Yes/No in
ordinary chat**. Yes selects them; it does not authorize installation. After
source integrity, a short destination and other readiness checks pass, approve
the required file preparation and installation separately.

The assistant verifies both ZIPs and the complete supplied manifest, safely
unpacks the **small operational ZIP only** into the new project root, and leaves
the large snapshot zipped beside the launcher. It preserves the older bundled
guide at its manifest-listed path but follows your attached v0.11 instead.

The supplied `workflow.py setup --consent-install` creates an isolated
environment and installs the pinned packages from the snapshot. Use its
printed interpreter for package checks and preflight.

## Approve and run the frozen computation

After successful setup/preflight, choose the offered run mode and separately
approve bounded computation. The native command is
`workflow.py run --approve-compute` with the verified isolated Python.
The guide's prompts and safety gates govern, not an implied approval in this README.

Default scope recomputes scoring, saved-model inference and ranking; it reuses
docking, training, evidence and narrative. No CLIO, fresh retrieval, new docking
or training is required. A historical first run was quiet for about 37 minutes
before stage 1; that is an observation, not a guaranteed duration. Follow the
guide's progress instructions, honor cancellation and preserve errors.

## Check the result

Use the **new run's** receipt, semantic acceptance, stage logs, dashboard and
100-structure exports. Historical files inside `validation/` are comparison
evidence, not this run's outputs. A previous frozen run passed seven listed
semantic comparisons; this updated customer route is still awaiting its own
recorded test. Keep the guide's scientific limitations visible.

The supplied v0.11 guide is a proposed revision. Windows x64 has historical
reference evidence; other scientific-platform routes are unvalidated.

## Supporting files

Place these six files beside the full v0.11 guide. Verify the checksums before
setup; a different snapshot is not interchangeable with this reference.

| Original filename | Purpose | Repository download |
| --- | --- | --- |
| `cdk_operational_bundle_v1.zip` | Small launcher bundle, 50,301 bytes | [Download](files/cdk_operational_bundle_v1.zip) |
| `cdk_operational_bundle_v1.sha256` | Exact checksum for the small ZIP | [Download](files/cdk_operational_bundle_v1.sha256) |
| `cdk_operational_bundle_v1_manifest.csv` | Original member paths, sizes and hashes | [Download](files/cdk_operational_bundle_v1_manifest.csv) |
| `cdk_operational_bundle_v1_validation.json` | Original handoff verification record | [Download](files/cdk_operational_bundle_v1_validation.json) |
| `cdk_non_md_deliverables.zip` | Exact frozen scientific snapshot, 168,396,900 bytes | [Download (contributor fork)](https://github.com/faranm-stack/discovery/releases/download/QuickStart-BioPharma-frozen-reference-v1/cdk_non_md_deliverables.zip) |
| `cdk_non_md_deliverables.sha256` | Exact checksum for the large ZIP | [Download](files/cdk_non_md_deliverables.sha256) |

The large ZIP's SHA-256 is
`bfc939735a574ccc2375ae9808e9242fb79efc545d5b3f314cc38e5d0cefa076`.
The snapshot exceeds GitHub's regular-file limit and is not included in a
repository checkout or source-code ZIP. The assistant retrieves it from the
versioned link above; customers do not need to navigate Releases themselves.
Keep the original filename: it is part of the frozen launcher's input contract.

### What is inside the small ZIP?

The ZIP contains `workflow.py`, `profile.json`, `launch.ps1`, `acceptance.py`,
the offline guard, tests, manifest, and a **validation folder** with reference
receipts and stage logs. Preserve all member paths, including the older guide;
follow the attached v0.11 guide for instructions. The launcher extracts the
large snapshot during the approved workflow.

Retain the snapshot's MIT campaign-code licence and source-specific data notices.