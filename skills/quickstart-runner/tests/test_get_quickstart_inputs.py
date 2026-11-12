"""Offline downloader contract tests; Python is maintainer-only, not a dependency.

Run with unittest or directly. PowerShell tests skip when pwsh is unavailable.
Set QS_TEST_SHELL to an installed powershell.exe to also test Windows PowerShell
5.1. Child shells use process-only RemoteSigned for this locally authored script;
machine/user execution policy is never changed. All approved flows replace the
HTTP function with a local byte-stream mock;
no test contacts GitHub, extracts archives, installs software, or runs science.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "Get-QuickStartInputs.ps1"
SHELL = shutil.which(os.environ.get("QS_TEST_SHELL", "pwsh"))
SHELL_ARGS = [SHELL, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "RemoteSigned"]
REVISION = "a1" * 20
REPOSITORY = "faranm-stack/discovery"
RELEASE = ("https://github.com/faranm-stack/discovery/releases/download/"
           "QuickStart-BioPharma-frozen-reference-v1/cdk_non_md_deliverables.zip")
NAMES = ["cdk_operational_bundle_v1.zip", "cdk_operational_bundle_v1.sha256",
         "cdk_operational_bundle_v1_manifest.csv", "cdk_operational_bundle_v1_validation.json",
         "cdk_non_md_deliverables.zip", "cdk_non_md_deliverables.sha256"]


def ps(value):
    return "'" + str(value).replace("'", "''") + "'"


@unittest.skipUnless(SHELL, "pwsh is unavailable; PowerShell execution tests skipped")
class GetQuickStartInputsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Fail once if the script cannot load, rather than misreporting negative
        # tests as passing merely because an execution policy blocked all code.
        result = subprocess.run(SHELL_ARGS + ["-Command", "$ErrorActionPreference='Stop'; . " + ps(SCRIPT)],
                                capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise RuntimeError("Script load failed: " + result.stdout + result.stderr)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="qsi-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dest = self.root / "new"
        self.fixture = self.root / "fixture"
        self.fixture.mkdir()
        self.inventory_path = self.root / "inventory.json"
        self.payloads = {name: b"opaque bytes\x00\xff\r\n" + name.encode() for name in NAMES}
        self.guide_name = "QuickStart-BioPharma LeadGen v0.11.md"
        self.payloads[self.guide_name] = b"\xef\xbb\xbf# Exact guide\r\nDo not execute.\r\n"
        self.payloads["quickstart-runner.md"] = b"# Shared skill\nRead, do not automatically run.\n"
        self.payloads["LICENSE"] = b"MIT test license\n"
        for name, data in self.payloads.items():
            (self.fixture / name).write_bytes(data)

        def entry(name, guide=False):
            data = self.payloads[name]
            return {"path": "quickstarts/biopharma/cdk-frozen-reference/" +
                    ("" if guide else "files/") + name, "name": name,
                    "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}

        self.record = {"publicationApproved": True, "guide": entry(self.guide_name, True),
                       "files": [entry(name) for name in NAMES], "releaseUrl": RELEASE}
        self.record["files"][4]["url"] = RELEASE
        self.inventory = {"formatVersion": 1, "packages": {"biopharma": self.record}}
        self.save_inventory()

    def save_inventory(self):
        self.inventory_path.write_text(json.dumps(self.inventory), encoding="utf-8")

    def run_ps(self, code, ok=True, mock=True):
        # Every test overrides HTTP even if it is expected never to reach it.
        setup = "$ErrorActionPreference = 'Stop'\n. " + ps(SCRIPT) + "\n"
        setup += "$script:Calls = New-Object 'System.Collections.Generic.List[string]'\n"
        if mock:
            setup += f"""
function Receive-QSHttp {{
    param([string]$Url, [IO.Stream]$OutputStream, [long]$MaxBytes, [string]$Label)
    $script:Calls.Add($Url)
    if ($Label -eq 'downloads.json') {{ $path = {ps(self.inventory_path)} }}
    else {{ $path = [IO.Path]::Combine({ps(self.fixture)}, $Label) }}
    $input = [IO.File]::OpenRead($path)
    try {{ return Copy-QSBoundedStream $input $OutputStream $MaxBytes $Label }}
    finally {{ $input.Dispose() }}
}}
"""
        else:
            setup += "function Receive-QSHttp { throw 'NETWORK MUST NOT BE CALLED' }\n"
        result = subprocess.run(SHELL_ARGS + ["-Command", setup + code],
                                capture_output=True, text=True, timeout=60)
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout + result.stderr

    def invocation(self, approve=True, destination=None, revision=REVISION, repository=REPOSITORY):
        return (f"Invoke-QSDownload -Repository {ps(repository)} -Revision {ps(revision)} "
                f"-Destination {ps(destination or self.dest)}" + (" -ApproveDownload" if approve else ""))

    def validation(self):
        return (f"$data = Get-Content -LiteralPath {ps(self.inventory_path)} -Raw | ConvertFrom-Json; "
                f"@(Get-QSEntries $data {ps(REPOSITORY)} {ps(REVISION)}) | ConvertTo-Json -Depth 8")

    def test_parser_and_command_allowlist(self):
        self.run_ps(f"""
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile({ps(SCRIPT)}, [ref]$tokens, [ref]$errors)
if ($errors.Count) {{ throw ($errors | Out-String) }}
$allowed = @('Get-Item','Test-Path','ForEach-Object','Where-Object','New-Object',
    'Write-Progress','ConvertFrom-Json','Write-Host','New-Item','ConvertTo-Json','Write-Error')
$commands = $ast.FindAll({{param($n) $n -is [Management.Automation.Language.CommandAst]}}, $true)
foreach ($command in $commands) {{
    $name = $command.GetCommandName()
    if (-not $name -or ($name -notmatch '^(Assert|Get|Copy|Receive|Save|Invoke)-QS' -and $name -notin $allowed)) {{
        throw "Unexpected executable command: $name"
    }}
}}
""")

    def test_dot_source_does_not_invoke_entrypoint(self):
        self.run_ps("if ($script:Calls.Count) { throw 'Unexpected calls' }", mock=False)
        self.assertFalse(self.dest.exists())

    def test_no_approval_no_network_or_writes(self):
        before = set(self.root.rglob("*"))
        output = self.run_ps(self.invocation(False), mock=False)
        self.assertIn("PLAN ONLY", output)
        self.assertIn(REVISION, output)
        self.assertLess(len(output), 2000)
        self.assertEqual(set(self.root.rglob("*")), before)

    def test_direct_script_default_is_plan_only(self):
        # Direct path exercises the invocation-style guard. No approved network.
        result = subprocess.run(SHELL_ARGS + ["-File", str(SCRIPT),
                                 "-Repository", REPOSITORY, "-Revision", REVISION,
                                 "-Destination", str(self.dest)], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PLAN ONLY", result.stdout)
        self.assertFalse(self.dest.exists())

    def test_direct_missing_revision_fails_without_prompt(self):
        result = subprocess.run(SHELL_ARGS + ["-File", str(SCRIPT),
                                 "-Repository", REPOSITORY, "-Destination", str(self.dest)],
                                capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.dest.exists())

    def test_valid_downloads_exact_bytes_and_private_path_free_receipt(self):
        output = self.run_ps(self.invocation() + "\nif ($script:Calls.Count -ne 10) { throw 'Wrong request count' }")
        self.assertIn("Downloaded-only", output)
        self.assertEqual({p.name for p in self.dest.iterdir()}, set(self.payloads) | {"QuickStart-download-receipt.json"})
        for name, data in self.payloads.items():
            self.assertEqual((self.dest / name).read_bytes(), data)
        raw = (self.dest / "QuickStart-download-receipt.json").read_text(encoding="utf-8")
        receipt = json.loads(raw)
        self.assertEqual(receipt["stage"], "downloaded-only")
        self.assertEqual(receipt["sourceCommit"], REVISION)
        self.assertNotIn(str(self.root), raw)
        self.assertNotIn(str(self.root).replace("\\", "\\\\"), raw)
        self.assertEqual(len(receipt["files"]), 9)
        for entry in receipt["files"]:
            payload = self.payloads[entry["name"]]
            self.assertEqual(entry["bytes"], len(payload))
            self.assertEqual(entry["sha256"], hashlib.sha256(payload).hexdigest())
            if entry["name"] == NAMES[4]:
                self.assertEqual(entry["url"], RELEASE)
            else:
                self.assertTrue(entry["url"].startswith(f"https://raw.githubusercontent.com/{REPOSITORY}/{REVISION}/"))
            self.assertIn(entry["path"], output)
        self.assertFalse((self.dest / "README.md").exists())
        self.assertFalse((self.dest / ".github").exists())

    def test_existing_destination_rejected_before_network(self):
        self.dest.mkdir()
        sentinel = self.dest / "keep.txt"
        sentinel.write_bytes(b"keep")
        output = self.run_ps(self.invocation(), ok=False, mock=False)
        self.assertIn("already exists", output)
        self.assertNotIn("NETWORK MUST NOT BE CALLED", output)
        self.assertEqual(sentinel.read_bytes(), b"keep")

    def test_existing_file_destination_rejected(self):
        self.dest.write_bytes(b"keep")
        self.run_ps(self.invocation(), ok=False, mock=False)
        self.assertEqual(self.dest.read_bytes(), b"keep")

    def test_missing_parent_rejected(self):
        self.run_ps(self.invocation(destination=self.root / "missing" / "child"), ok=False, mock=False)
        self.assertFalse((self.root / "missing").exists())

    def test_short_local_destination_rules(self):
        for bad in ["relative", "//server/share/new", "\\\\server\\share\\new",
                    str(self.root / ("x" * 121)), str(self.root / "CON"), str(self.root / "bad.")]:
            with self.subTest(destination=bad):
                self.run_ps(self.invocation(destination=bad), ok=False, mock=False)

    def test_reparse_ancestor_rejected(self):
        target = self.root / "target"
        target.mkdir()
        link = self.root / "link"
        if os.name == "nt":
            self.run_ps(f"New-Item -ItemType Junction -Path {ps(link)} -Target {ps(target)} | Out-Null")
            self.addCleanup(lambda: os.rmdir(link) if link.exists() else None)
        else:
            link.symlink_to(target, target_is_directory=True)
        output = self.run_ps(self.invocation(destination=link / "new"), ok=False, mock=False)
        self.assertIn("Symlink/reparse", output)
        self.assertFalse((target / "new").exists())

    def test_nonpublication_inventory_no_files_created(self):
        for value in [False, "true", 1, None]:
            with self.subTest(publicationApproved=value):
                self.record["publicationApproved"] = value
                self.save_inventory()
                output = self.run_ps(self.invocation(), ok=False)
                self.assertIn("publicationApproved", output)
                self.assertFalse(self.dest.exists())

    def test_bad_inventory_format(self):
        for value in [2, "1", None]:
            with self.subTest(formatVersion=value):
                self.inventory["formatVersion"] = value
                self.save_inventory()
                self.run_ps(self.validation(), ok=False)

    def test_exact_revision_and_repository_validation(self):
        for value in ["main", "v1", "a" * 39, "a" * 41, "g" * 40, REVISION + "\n"]:
            with self.subTest(revision=value):
                self.run_ps(self.invocation(revision=value), ok=False, mock=False)
        for value in ["owner/../repo", "https://github.com/owner/repo", "owner/repo?x", "owner/repo/", "../repo"]:
            with self.subTest(repository=value):
                self.run_ps(self.invocation(repository=value), ok=False, mock=False)

    def test_valid_uppercase_commit_and_raw_url_encoding(self):
        out = self.run_ps(f"Get-QSRawUrl {ps(REPOSITORY)} {ps(REVISION.upper())} 'docs/Some Guide.md'")
        self.assertIn(REVISION.upper() + "/docs/Some%20Guide.md", out)

    def test_traversal_and_reserved_source_paths(self):
        invalid = ["../escape", "/absolute", "a/../b", "a//b", "a\\b", "a/%2e%2e/b", "a/NUL.txt",
                   "CON", "con.csv", "a/LPT1", "C:/path", "a/trailing.", "a/trailing ", "a:stream"]
        # Batch independent assertions into one offline process.
        cases = ",".join(ps(v) for v in invalid)
        self.run_ps(f"foreach ($value in @({cases})) {{ $failed=$false; try {{ Assert-QSPath $value }} catch {{ $failed=$true }}; if (-not $failed) {{ throw \"Accepted $value\" }} }}")

    def test_duplicate_names_and_paths_rejected_before_write(self):
        self.record["files"][1] = copy.deepcopy(self.record["files"][0])
        self.save_inventory()
        output = self.run_ps(self.invocation(), ok=False)
        self.assertIn("Duplicate", output)
        self.assertFalse(self.dest.exists())

    def test_case_insensitive_duplicate_rejected(self):
        duplicate = copy.deepcopy(self.record["files"][0])
        duplicate["name"] = duplicate["name"].upper()
        duplicate["path"] = duplicate["path"].upper()
        self.record["files"][1] = duplicate
        self.save_inventory()
        self.assertIn("Duplicate", self.run_ps(self.validation(), ok=False))

    def test_exact_six_names_and_guide(self):
        original = copy.deepcopy(self.record)
        mutations = [lambda: self.record["files"].pop(),
                     lambda: self.record["files"].append(copy.deepcopy(self.record["files"][0])),
                     lambda: self.record.update(guide=None),
                     lambda: self.record["files"][0].update(name="unexpected.zip", path="files/unexpected.zip"),
                     lambda: self.record["guide"].update(name="README.md", path="README.md"),
                     lambda: self.record["guide"].update(path="different.md")]
        for mutate in mutations:
            self.record.clear()
            self.record.update(copy.deepcopy(original))
            mutate()
            self.save_inventory()
            self.run_ps(self.validation(), ok=False)

    def test_invalid_sizes_and_hash_formats(self):
        original = copy.deepcopy(self.record["files"][0])
        for field, values in [("bytes", [0, -1, 1.5, "12", True, 2147483649, None]),
                              ("sha256", ["a" * 63, "a" * 65, "g" * 64, "a" * 64 + "\n", None])]:
            for value in values:
                with self.subTest(field=field, value=value):
                    self.record["files"][0] = dict(original, **{field: value})
                    self.save_inventory()
                    self.run_ps(self.validation(), ok=False)

    def test_hash_mismatch_preserves_partial(self):
        self.record["guide"]["sha256"] = "0" * 64
        self.save_inventory()
        output = self.run_ps(self.invocation(), ok=False)
        self.assertIn("SHA-256 mismatch", output)
        self.assertEqual((self.dest / (self.guide_name + ".partial")).read_bytes(), self.payloads[self.guide_name])
        self.assertFalse((self.dest / self.guide_name).exists())
        self.assertFalse((self.dest / "QuickStart-download-receipt.json").exists())

    def test_short_body_preserves_partial(self):
        (self.fixture / self.guide_name).write_bytes(b"short")
        self.assertIn("Length mismatch", self.run_ps(self.invocation(), ok=False))
        self.assertEqual((self.dest / (self.guide_name + ".partial")).read_bytes(), b"short")

    def test_oversize_body_reads_only_expected_plus_one(self):
        size = self.record["guide"]["bytes"]
        (self.fixture / self.guide_name).write_bytes(b"X" * (size + 100000))
        self.run_ps(self.invocation(), ok=False)
        self.assertEqual((self.dest / (self.guide_name + ".partial")).stat().st_size, size + 1)
        self.assertFalse((self.dest / self.guide_name).exists())

    def test_existing_partial_is_never_overwritten(self):
        self.dest.mkdir()
        partial = self.dest / (self.guide_name + ".partial")
        partial.write_bytes(b"keep")
        code = self.validation().split("; @")[0] + f"; $entry = @(Get-QSEntries $data {ps(REPOSITORY)} {ps(REVISION)})[0]; Save-QSFile $entry {ps(self.dest)}"
        self.run_ps(code, ok=False, mock=False)
        self.assertEqual(partial.read_bytes(), b"keep")

    def test_existing_target_is_never_overwritten(self):
        self.dest.mkdir()
        target = self.dest / self.guide_name
        target.write_bytes(b"keep")
        code = self.validation().split("; @")[0] + f"; $entry = @(Get-QSEntries $data {ps(REPOSITORY)} {ps(REVISION)})[0]; Save-QSFile $entry {ps(self.dest)}"
        self.run_ps(code, ok=False, mock=False)
        self.assertEqual(target.read_bytes(), b"keep")

    def test_shared_skill_and_license_nonempty_and_bounded(self):
        for name, data in [("quickstart-runner.md", b""), ("LICENSE", b"X" * 1048577)]:
            with self.subTest(name=name):
                for file, payload in self.payloads.items():
                    (self.fixture / file).write_bytes(payload)
                (self.fixture / name).write_bytes(data)
                dest = self.root / ("empty" if not data else "large")
                self.run_ps(self.invocation(destination=dest), ok=False)
                self.assertTrue((dest / (name + ".partial")).exists())
                self.assertFalse((dest / name).exists())
                self.assertFalse((dest / "QuickStart-download-receipt.json").exists())

    def test_inventory_body_limit(self):
        self.inventory_path.write_bytes(b" " * 1048577)
        self.run_ps(self.invocation(), ok=False)
        self.assertFalse(self.dest.exists())

    def test_external_url_only_allowed_for_snapshot(self):
        self.record["guide"]["url"] = RELEASE
        self.save_inventory()
        self.assertIn("Only the snapshot", self.run_ps(self.invocation(), ok=False))
        self.assertFalse(self.dest.exists())

    def test_release_record_must_match_snapshot(self):
        self.record["releaseUrl"] = RELEASE.replace("reference-v1", "reference-v2")
        self.save_inventory()
        self.assertIn("releaseUrl", self.run_ps(self.validation(), ok=False))

    def test_release_https_host_version_and_authority_rules(self):
        invalid = [RELEASE.replace("https:", "http:"), RELEASE.replace("github.com", "evil.test"),
                   RELEASE.replace("github.com", "github.com.evil.test"),
                   RELEASE.replace("github.com", "user:pass@github.com"),
                   RELEASE.replace("github.com", "github.com:443"), RELEASE + "?token=x", RELEASE + "#x",
                   RELEASE.replace("QuickStart-BioPharma-frozen-reference-v1", "latest"),
                   RELEASE.replace("QuickStart-BioPharma-frozen-reference-v1", "main"),
                   RELEASE.replace("QuickStart-BioPharma-frozen-reference-v1", ".."),
                   RELEASE.replace("/download/", "/latest/download/"),
                   RELEASE.replace("QuickStart-BioPharma-frozen-reference-v1", "%2e%2e")]
        cases = ",".join(ps(v) for v in invalid)
        self.run_ps(f"Assert-QSReleaseUrl {ps(RELEASE)}; foreach ($url in @({cases})) {{ $failed=$false; try {{ Assert-QSReleaseUrl $url }} catch {{ $failed=$true }}; if (-not $failed) {{ throw \"Accepted $url\" }} }}")

    def test_redirect_host_is_restricted_to_github_delivery(self):
        cdn = "https://release-assets.githubusercontent.com/github-production-release-asset/1/asset?sig=example"
        invalid = ["https://evil.test/file", "http://release-assets.githubusercontent.com/github-production-release-asset/x",
                   "https://user@release-assets.githubusercontent.com/github-production-release-asset/x",
                   "https://release-assets.githubusercontent.com:443/github-production-release-asset/x",
                   "https://release-assets.githubusercontent.com.evil.test/github-production-release-asset/x",
                   "https://release-assets.githubusercontent.com/other/x", "https://github.com/login",
                   "https://raw.githubusercontent.com/owner/repo/main/file", "https://raw.githubusercontent.com:443/o/r/" + REVISION + "/file"]
        cases = ",".join(ps(v) for v in invalid)
        self.run_ps(f"Assert-QSTransportUrl {ps(cdn)} {ps(RELEASE)}; foreach ($url in @({cases})) {{ $failed=$false; try {{ Assert-QSTransportUrl $url {ps(RELEASE)} }} catch {{ $failed=$true }}; if (-not $failed) {{ throw \"Accepted $url\" }} }}")
        self.run_ps(f"Assert-QSTransportUrl {ps(cdn)}", ok=False)


if __name__ == "__main__":
    unittest.main(verbosity=2)