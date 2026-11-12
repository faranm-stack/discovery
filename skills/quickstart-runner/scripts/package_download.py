"""Prepare exact-file QuickStart downloads locally; never install, extract or upload.

Python 3.10+ standard library. Exit 0: requested byte operation succeeded;
1: invalid input; 2: missing files/guide or unapproved publication mode.
The owner guides and nested archives are copied as bytes without modification.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
from urllib.parse import quote
import zipfile

SCENARIOS = ("biopharma", "siphy", "cms", "bookshelf")
PRIVATE = {".git", ".discovery", ".local", "sessions", "results", "__pycache__", "node_modules", ".env"}
RESERVED = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?\Z", re.I)
HASH = re.compile(r"[0-9a-f]{64}\Z")
GENERATED = {"readme.md", "license", "quickstart-runner.md", "package-sha256sums.txt"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def no_links(path):
    for item in (path, *path.parents):
        require(not item.is_symlink(), f"Symlink refused: {item.name}")
        if item.exists():
            require(not getattr(item.lstat(), "st_file_attributes", 0) & 0x400,
                    f"Reparse point refused: {item.name}")


def safe_path(root, value):
    require(isinstance(value, str) and value, "Nonempty relative path required")
    require(not PurePosixPath(value).is_absolute() and "\\" not in value, "Absolute/backslash path refused")
    parts = value.split("/")
    for part in parts:
        require(part not in ("", ".", "..") and not part.endswith((".", " "))
                and not RESERVED.fullmatch(part)
                and not any(c in ':*?<>|"' or ord(c) < 32 for c in part), "Unsafe path component")
        require(part.casefold() not in PRIVATE and not part.casefold().startswith(".venv"),
                "Runtime state or private environment cannot be packaged")
    root = Path(root).absolute()
    path = root.joinpath(*parts)
    no_links(path)
    require(path.resolve().is_relative_to(root.resolve()), "Path escapes root")
    return path


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def pairs(items):
    result = {}
    for key, value in items:
        require(key not in result, f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def read_json(path):
    no_links(path)
    return json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=pairs)


def load(root, scenario):
    require(scenario in SCENARIOS, "Unknown scenario")
    data = read_json(safe_path(root, "skills/quickstart-runner/references/downloads.json"))
    require(isinstance(data, dict) and data.get("formatVersion") == 1, "Unknown inventory format")
    record = data["packages"][scenario]
    require(isinstance(record, dict) and isinstance(record.get("title"), str), "Invalid package")
    require(type(record.get("publicationApproved")) is bool, "Explicit publication status required")
    require(isinstance(record.get("files"), list), "Files array required")
    guide = record.get("guide")
    require(guide is None or isinstance(guide, dict), "Invalid guide record")
    entries = ([guide] if guide else []) + record["files"]
    if record.get("paperManifest"):
        manifest = safe_path(root, record["paperManifest"])
        for paper in read_json(manifest):
            name = f"{paper['pmcid']}.{paper['version']}.pdf"
            entries.append({"path": str(PurePosixPath(record["paperManifest"]).parent / "papers" / name),
                            "name": name, "bytes": paper["bytes"], "sha256": paper["sha256"]})
    names, sources = set(GENERATED), set()
    for entry in entries:
        require(isinstance(entry, dict), "File record must be an object")
        name = entry.get("name")
        path = safe_path(root, entry.get("path"))
        safe_path(root, name)
        require("/" not in name and name.casefold() not in names, "Duplicate/reserved download filename")
        require(entry["path"].casefold() not in sources, "Duplicate source file")
        names.add(name.casefold())
        sources.add(entry["path"].casefold())
        require(type(entry.get("bytes")) is int and entry["bytes"] > 0, "Positive byte length required")
        require(isinstance(entry.get("sha256"), str) and HASH.fullmatch(entry["sha256"]), "Exact SHA256 required")
    return record, entries


def check(root, scenario):
    try:
        root = Path(root).absolute()
        record, entries = load(root, scenario)
        results = []
        for entry in entries:
            path = safe_path(root, entry["path"])
            status = "missing" if not path.is_file() else (
                "verified" if path.stat().st_size == entry["bytes"] and digest(path) == entry["sha256"] else "mismatch")
            results.append({"file": entry["name"], "status": status})
        mismatch = any(item["status"] == "mismatch" for item in results)
        missing = any(item["status"] == "missing" for item in results)
        return {"status": "failed" if mismatch else "blocked" if missing else "passed",
                "scope": "exact-local-bytes-only", "scenario": scenario, "files": results,
                "completedGuidePresent": record["guide"] is not None,
                "publicationReady": bool(record["guide"] and not mismatch and not missing and record["publicationApproved"]),
                "publicationNote": record.get("publicationReason", "Not approved"),
                "scientificExecution": "not-run"}
    except (OSError, ValueError, KeyError, TypeError, UnicodeError) as error:
        return {"status": "failed", "errors": [str(error)], "scope": "exact-local-bytes-only"}


def biopharma_start(guide_name):
    """Concrete Windows Stable steps for the supplied BioPharma v0.11 candidate."""
    return (
        "### A. Extract the download\n\n"
        "In Windows File Explorer, right-click the downloaded test ZIP and choose **Extract All**. "
        "Choose a new short folder in Downloads, for example **QuickStart-BioPharma**, then click **Extract**. "
        "Open the extracted folder: you should see this README, the two Markdown attachments below, "
        "and the six input files. Leave the two inner ZIPs unopened.\n\n"
        "### B. Open the folder as a Discovery project\n\n"
        "1. Open **Microsoft Discovery Stable**, not Preview.\n"
        "2. If another project is open, use the project dropdown beside its name to return to the project selector. "
        "Choose the local-project view if a workspace choice is shown.\n"
        "3. Click **New project**. In the folder picker, navigate to **Downloads**, open the extracted "
        "**QuickStart-BioPharma** folder, and click **Open local project**. Select the folder containing this README, "
        "not the ZIP and not an extra parent folder. A folder picker may hide individual files; that is normal.\n"
        "4. Confirm the project name/path refers to the extracted folder. Click **New session** in the left sidebar. "
        "If the app asks you to initialize the local project, complete that step in this new folder only.\n\n"
        "These labels were checked against installed Windows Stable 0.0.4. If your build differs, "
        "stop and record the screen rather than guessing a cloud deployment or changing an existing project.\n\n"
        "### C. Choose the model\n\n"
        "In the new chat's model selector, choose **GPT-6 Astra (`gpt-6-astra`)**. "
        "Do not choose Sol or a similarly named model for this test. If Astra is not listed, stop: "
        "the account needs access before this reference profile can run. Selecting a model does not install Python.\n\n"
        "### D. Attach exactly two instruction files\n\n"
        "Yes—**two Markdown files are attached to the chat**:\n\n"
        "| File | Why it is needed |\n| --- | --- |\n"
        "| [quickstart-runner.md](quickstart-runner.md) | Tells the assistant how to guide setup, approvals, execution and verification. |\n"
        f"| [{guide_name}]({quote(guide_name)}) | Supplies the exact BioPharma procedure, package versions, prompt and acceptance checks. |\n\n"
        "1. Use the chat input's attachment control and choose files (not a folder).\n"
        "2. Browse to **Downloads → QuickStart-BioPharma**. Hold **Ctrl** and click the two files named above.\n"
        "3. Click **Attach files**, then check that both filenames appear as attachments before sending. "
        "If the UI adds one file at a time, repeat for the second file.\n\n"
        "Do **not** attach the README, checksum files, or either input ZIP to the chat; keep those in the project folder "
        "for the assistant's local file tools. Do not copy Markdown text into a Python terminal. "
        "This is explicit instruction loading, not a passed automatic skill-discovery test; no slash command is needed.\n\n"
        "### E. Send this starting instruction\n\n"
        "> Follow the attached quickstart-runner instructions and full BioPharma v0.11 guide using the files in this new project. "
        "Run the default frozen-reference scenario in section 5, not the fresh-discovery extension in section 6. "
        "First confirm you can read both attachments and check the model, Python, input hashes and local execution access. "
        "Keep file preparation, dependency selection, installation and computation approvals separate. "
        "Stop and explain missing requirements; do not reuse another project's environment or results.\n\n"
        "### F. Python and dependency setup\n\n"
        "**Python is not installed automatically by this package or by the supplied launcher.** "
        "The assistant checks for **Python 3.13.14, 64-bit**, and pauses if it is missing or different. "
        "Having Python 3.13.17 does not satisfy this exact profile.\n\n"
        "- If the required Python is already installed, let the assistant verify its exact executable and architecture.\n"
        "- If it is missing, the guide's one-time setup directs you to "
        "[Python 3.13.14](https://www.python.org/downloads/release/python-31314/). "
        "On Windows x64, choose **Windows installer (64-bit)**, not the install manager, 32-bit or embeddable download. "
        "Follow the guide's per-user installation instructions, then ask the assistant to recheck.\n"
        "- If the installer reports a newer version is present, **stop and record the message**. "
        "Do not uninstall/downgrade it, alter pins, or reuse the old tested project's environment to bypass the check. "
        "A separately provisioned exact interpreter needs its own approved setup.\n"
        "- The assistant displays all **14 required packages** with versions and descriptions. "
        "Reply **Yes** or **No** in ordinary chat to select the complete set. Yes does not install anything.\n"
        "- After other readiness checks pass, approve installation separately. The assistant then runs the supplied "
        "setup to create an isolated environment and install those packages plus required supporting dependencies. "
        "It verifies the environment and preflight before asking to compute.\n\n"
        "**Ready when:** Astra, Python 3.13.14 64-bit, verified inputs, required local tools and the native preflight "
        "all pass. File preparation opens only the small operational ZIP; the launcher handles the scientific snapshot later. "
        "No science starts until you separately approve computation.\n\n"
    )


def prepare(root, scenario, destination, local_review=False, apply=False):
    root, dest = Path(root).absolute(), Path(destination).absolute()
    report = check(root, scenario)
    if report["status"] != "passed":
        return report
    try:
        record, entries = load(root, scenario)
        if record["guide"] is None:
            return {"status": "blocked", "errors": ["Completed owner QuickStart guide not supplied; no substitute created"]}
        if not local_review and not record["publicationApproved"]:
            return {"status": "blocked", "errors": ["Public distribution not approved; --local-review only prepares a local author copy"]}
        archive = dest.with_name(dest.name + ".zip")
        no_links(dest)
        no_links(archive)
        require(not dest.exists() and not archive.exists(), "Destination or ZIP already exists; nothing overwritten")
        require(dest != archive and dest.parent.is_dir(), "Choose a folder with an existing parent")
        require(not dest.resolve().is_relative_to(root.resolve()), "Review destination must be outside repository")
        runner = safe_path(root, "skills/quickstart-runner/SKILL.md")
        licence = safe_path(root, "LICENSE")
        require(runner.is_file() and licence.is_file(), "Runner or repository licence missing")
        if not apply:
            return {"status": "planned", "scenario": scenario, "files": [x["name"] for x in entries],
                    "destination": str(dest), "zip": str(archive), "writes": False}
        dest.mkdir()
        copied = []
        for entry in entries:
            source = safe_path(root, entry["path"])
            target = safe_path(dest, entry["name"])
            with source.open("rb") as src, target.open("xb") as out:
                shutil.copyfileobj(src, out)
            require(target.stat().st_size == entry["bytes"] and digest(target) == entry["sha256"], "Source changed during copy; preserve partial output")
            copied.append(entry["name"])
        for source, name in ((runner, "quickstart-runner.md"), (runner, ".github/skills/quickstart-runner/SKILL.md"), (licence, "LICENSE")):
            target = safe_path(dest, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open("rb") as src, target.open("xb") as out:
                shutil.copyfileobj(src, out)
            require(digest(source) == digest(target), "Shared file copy mismatch")
            copied.append(name)
        guide_name = record["guide"]["name"]
        links = "\n".join(f"- [{x['name']}]({quote(x['name'])})" for x in entries)
        start = (
            f"# QuickStart {record['title']} — start here\n\n"
            "**Local test candidate — not a public release or a validated result.** "
            "All listed inputs are included for this local test; public download availability is a separate check.\n\n"
            "## 1. Set up and confirm readiness\n\n"
            "1. If starting from the download ZIP, extract that outer ZIP into a new short local folder. "
            "Leave the scenario's ZIP/tar files inside it unopened until the guide directs preparation.\n"
            "2. Open Microsoft Discovery Stable and open/create a local project using that folder. "
            "Start a new session; do not reuse an existing project or environment.\n"
            f"3. Select the model required by [{guide_name}]({quote(guide_name)}).\n"
            "4. Attach both Markdown files to the chat using its attachment control: "
            f"[quickstart-runner.md](quickstart-runner.md) and [{guide_name}]({quote(guide_name)}). "
            "This is the explicit-loading route; no slash command or personal preinstalled skill is required. "
            "If the project skill is also discovered, it is the same runner, not a second workflow.\n"
            "5. Send the instruction below. The assistant must confirm it can read both attachments, "
            "check the input checksums and runtime, and report any blockers before installation.\n\n"
            "> Follow the attached quickstart-runner instructions and full QuickStart guide using only the supplied files in this new project. "
            "Use the guide's default scenario, not an optional extension. Start with readiness checks and keep file preparation, "
            "dependency selection, installation and computation approvals separate. Stop and explain any missing requirement; "
            "do not use another project's files or silently substitute versions.\n\n"
            "**Ready when:** the guide's required model, runtime, inputs, capabilities and preflight checks pass. "
            "Python packages and Python itself are separate installations; an installed but different version is a blocker, "
            "not permission to downgrade it. Review each scoped approval; do not automatically allow every request.\n\n"
            "## 2. Execute\n\n"
            "After setup passes, approve only the guide's bounded computation. The runner follows the full guide's "
            "fixed prompt, stages and limits and reports progress. A quiet process is not permission to start a duplicate run. "
            "If the host times out, preserve the error and determine whether work is still running before any retry.\n\n"
            "## 3. Verify\n\n"
            "Check the new run's outputs against the guide's validated reference and acceptance criteria. "
            "Open the resulting report/dashboard and check its links. Report readiness, execution and acceptance separately; "
            "historical outputs and a chat PASS alone do not prove a new successful run.\n\n"
            f"## Original files\n\n{links}\n\n"
            f"## Current limits\n\n{record.get('runtimeStatus', 'Runtime not validated')}\n\n"
            f"{record.get('publicationReason', 'Publication not approved')}\n\n"
            "Nested archives remain unopened. No project state, installed environment or scientific execution was created by packaging.\n"
        )
        if scenario == "biopharma":
            setup_heading = "## 1. Set up and confirm readiness\n\n"
            setup_start = start.index(setup_heading) + len(setup_heading)
            setup_end = start.index("## 2. Execute\n\n", setup_start)
            start = start[:setup_start] + biopharma_start(guide_name) + start[setup_end:]
        with (dest / "README.md").open("x", encoding="utf-8", newline="\n") as out:
            out.write(start)
        copied.append("README.md")
        checksum = "".join(f"{digest(safe_path(dest, name))}  {name}\n" for name in sorted(copied))
        with (dest / "PACKAGE-SHA256SUMS.txt").open("x", encoding="utf-8", newline="\n") as out:
            out.write(checksum)
        copied.append("PACKAGE-SHA256SUMS.txt")
        # Store already-compressed payloads without altering or expanding them.
        with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_STORED) as outer:
            for name in sorted(copied):
                outer.write(safe_path(dest, name), name)
        with zipfile.ZipFile(archive) as outer:
            require(outer.testzip() is None, "Outer download failed CRC verification")
            for entry in entries:
                hasher = hashlib.sha256()
                with outer.open(entry["name"]) as incoming:
                    for block in iter(lambda: incoming.read(1024 * 1024), b""):
                        hasher.update(block)
                require(hasher.hexdigest() == entry["sha256"], "Source bytes changed inside outer download")
        return {"status": "prepared-local-review", "scenario": scenario, "destination": str(dest),
                "zip": str(archive), "zipBytes": archive.stat().st_size, "zipSha256": digest(archive),
                "originalFilesVerified": len(entries), "scientificExecution": "not-run", "uploaded": False}
    except (OSError, ValueError, KeyError, TypeError, UnicodeError, zipfile.BadZipFile) as error:
        return {"status": "failed", "errors": [str(error)],
                "note": "Preserve any partial new output; original sources and existing destinations are never overwritten"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("check", "prepare"):
        command = commands.add_parser(name)
        command.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[3])
        command.add_argument("--scenario", choices=SCENARIOS, required=True)
        if name == "prepare":
            command.add_argument("--destination", type=Path, required=True)
            command.add_argument("--local-review", action="store_true")
            command.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    result = check(args.repo_root, args.scenario) if args.command == "check" else prepare(
        args.repo_root, args.scenario, args.destination, args.local_review, args.apply)
    print(json.dumps(result, indent=2, ensure_ascii=True))
    return 1 if result["status"] == "failed" else 2 if result["status"] == "blocked" else 0


if __name__ == "__main__":
    sys.exit(main())