# QuickStart runner — how to load it

Start at [QuickStart Scenarios](../../quickstarts/README.md) and select a scenario.

The [SKILL.md](SKILL.md) file tells the assistant how to follow the full guide
you supply: check what is missing, request the right approvals, explain each
phase, follow the documented steps and inspect the outputs. It is not a Python
program, an app installer or a background engine. The full scenario Markdown
still specifies the inputs, model, scientific procedure and success criteria.

Use **this shared skill and the selected full guide**. Scenario-specific
requirements and acceptance criteria come from the guide. The optional original
Silicon and Physics (SiPhy) runner is a separate version, not an additional setup step.

## Start from a GitHub folder link

For [QuickStart BioPharma](../../quickstarts/biopharma/cdk-frozen-reference/README.md#start-here--give-discovery-this-folder-link),
give the assistant the scenario folder URL in a fresh local project. The README
provides a copyable starting prompt. The assistant reads the full guide and this
runner at the folder's resolved commit, requests download consent, and uses the
Windows download-only helper to retrieve and verify exact inputs. The scientist
does not need to clone Git, assemble attachments or unzip files for this route.
Network/file/command access is required; no automatic registration or end-to-end
success is implied. Other scenarios do not inherit BioPharma's downloader.

## Optional explicit-file loading in a fresh project

1. Obtain the selected full guide and every input listed in its scenario README.
2. Put them in a fresh local folder. Open/create a Discovery project there.
3. Download [SKILL.md](SKILL.md) using GitHub's **Download raw file** control.
   In the new chat, use the attachment control to attach that file and the full
   scenario guide. Locally prepared packages name the visible runner copy
   `quickstart-runner.md`; it contains the same instructions.
4. Send the scenario README's starting instruction. Confirm the assistant can
   read both attachments and follows the runner before approving installation.

This is **explicit instruction loading**, not proof of automatic skill discovery.
It does not require guessing a slash command or having a personal skill installed.
Project-skill loading through `.github/skills/quickstart-runner/SKILL.md` is an
alternative where supported; verify activation in a new session before relying
on that route. Do not run both copies as separate workflows.

## Version and compatibility

This guide-driven runner is version **0.3.0**. Fresh-project automatic activation
and end-to-end behavior have not been validated for this version. Manual loading
must be distinguished from automatic discovery when recording a run.

For SiPhy, follow the v1.1 guide over conflicting older instructions. See the
[original runner notes](../../quickstarts/siphy/spec-to-rtl/README.md#optional-original-runner)
before choosing that alternative.

## For maintainers only

See [preparing downloads](references/README.md) for exact-file verification and
local bundle creation. These utilities do not install dependencies, publish
downloads, or execute scenarios.