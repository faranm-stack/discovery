// NEW isolated-fork probe; never run this file on the credential-bearing review host.
// Source inspection only until CI executes: microsoft/discovery PR163 at SOURCE.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import {
  lstat, mkdir, readFile, readdir, rename, rm, writeFile,
} from 'node:fs/promises';
import { basename, join, relative, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const SOURCE = '7bc7e6af8b0d29cdf2453025c6dcaed9b435c240';
const RUNTIME = '/app/runtime';
const RETAINED = join(RUNTIME, 'generated/retained/hidden-flow-v1/runtime');
const RESULTS = '/results/hidden';
const SELF = fileURLToPath(import.meta.url);
const SCENARIO = join(RUNTIME, 'flowblind-scenario-harness-v2.mjs');
const ACTIVATION = join(RUNTIME,
  'generated/data/manifests/flowblind-private-hidden-flow-v1-activation-manifest-v1.json');
const EXAMPLE = join(RETAINED, 'generated/data/examples/exact-null-component.json');
const GOAL = 'Bound an unobserved hidden flow perturbation.';
const CASE_MS = 30_000;
const COLLECTION_MS = 210_000;
const RUNTIME_CASE_COUNT = 6;
const OUTPUT_BYTES = 16 * 1024 * 1024;
const PINS = Object.freeze([
  [SCENARIO, '59ab3b7bfed4a102eb399b74aaf3a523439855f52515988067e526d32dcf56b1'],
  [ACTIVATION, '4d596e0cb8dee8a6c0402e3f2233d5e224ebf669284cbad0a98b5652d7a6ab3d'],
  [EXAMPLE, '70e36e5f4ac8cad0f8c97cbe3a0f516e194493bb59be73a06d0fc2c9df8bea40'],
  [join(RETAINED, 'flowblind-hidden-flow-capability-verification-v1.mjs'),
    '4ebb0bf8db8e4b22345f892c04f4e5e8b02417c041af992b69c122d3a2a22e0d'],
  [join(RETAINED, 'generated/flowblind-catalog-hidden-flow-runtime-v1.mjs'),
    '29b7cf20974a9f1c5f42fff87ede181bcb848eeb2ffd70425a4d0f98a2d1b37f'],
  [join(RETAINED, 'generated/data/flowblind-catalog-hidden-flow-package-lock-v1.json'),
    '91b2644f438ec6e98593476412429b7a0203c4f3f8cde6f4849e6e1cb1507d48'],
]);
const COVERAGE_GAPS = Object.freeze([
  'No direct certificate-injection check: the shipped attested module does not export the inner verifier or solver injection. No internal code is extracted, evaluated, or patched.',
  'A native unavailable response can discard solver results and underlying errors. Preserve that response; report native status as unexposed, never infer DualInfeasible or PrimalInfeasible.',
  'The two new counterexamples exercise the retained native capability API, not the outer v2 route. The four original cases exercise the real v2 scenario harness.',
  'No continuous-domain quadrature-accuracy assertion: its scientific interpretation is conditional, not a confirmed defect.',
]);
const CASES = Object.freeze([
  {
    id: 'V2-HID-007',
    requirement: 'Original numerical scenario on the unchanged bundled example.',
    expected: 'report-complete; kind=numerical; containsResults=true; finite mm/s value; matching native evidence and deterministic replay.',
  },
  {
    id: 'V2-HID-008',
    requirement: 'Original contradiction scenario must certify incompatibility.',
    expected: 'report-complete; kind=infeasible; containsResults=true; evidenceState=verified-infeasible-within-tolerance.',
  },
  {
    id: 'V2-HID-009',
    requirement: 'Original no-budget scenario must report no finite bound.',
    expected: 'report-complete; kind=unavailable; containsResults=false; evidenceState=verified-unbounded-within-tolerance; reason=no-finite-bound.',
  },
  {
    id: 'V2-HID-014',
    requirement: 'Original scenario plus fresh-process resource reads and restart.',
    expected: 'Numerical result; two fresh resource reads match the original exact bytes; restarted run has the same URI/outcome; published files, markers, and mtimes unchanged.',
  },
  {
    id: 'PR163-HID-MIXED-POINT-SPEED',
    requirement: 'A feasible one-basis speed problem is unbounded even with zero x support.',
    expected: 'Preparation accepts one basis and four requests; report-complete; unavailable/no-finite-bound/verified-unbounded-within-tolerance; containsResults=false. A mixed-direction verification failure does NOT satisfy this expectation.',
  },
  {
    id: 'PR163-HID-ZERO-ROW-OBSERVATION',
    requirement: 'The inconsistent observation 0*c=1 must never be accepted as unbounded.',
    expected: 'Preparation accepts one basis and one request. Either certified infeasible (report-complete, containsResults=true), or unavailable with containsResults=false and verification-not-accepted/deterministic-replay-mismatch (accepted INCONCLUSIVE). Runtime/identity/resource/cancellation failures are Blocked, not a scientific pass.',
  },
  {
    id: 'PR163-HID-DIRECT-CERTIFICATE-INJECTION',
    requirement: 'Directly challenge inner certificate acceptance through an original supported API.',
    expected: 'Exercise the original exported verifier with a controlled certificate, or mark Blocked when the shipped API does not expose this operation. Never extract/eval internals, inject a replacement solver, or rewrite bundle code.',
  },
].map(Object.freeze));

const sha256 = (bytes) => createHash('sha256').update(bytes).digest('hex');

function canonicalProblemBytes(problem) {
  return Buffer.from(`${JSON.stringify(problem, (_key, value) => (
    value !== null && typeof value === 'object' && !Array.isArray(value)
      ? Object.fromEntries(Object.keys(value).sort().map((key) => [key, value[key]]))
      : value
  ), 2)}\n`);
}

async function save(path, value, replace = false) {
  const content = `${JSON.stringify(value, null, 2)}\n`;
  if (!replace) return writeFile(path, content, { flag: 'wx' });
  await writeFile(`${path}.next`, content);
  await rename(`${path}.next`, path);
}

function errorRecord(error, depth = 0) {
  return {
    name: error?.name ?? typeof error,
    code: error?.code ?? null,
    message: String(error?.message ?? error),
    stack: error?.stack ?? null,
    ...(error?.cause !== undefined && depth < 4
      ? { cause: errorRecord(error.cause, depth + 1) } : {}),
  };
}

async function snapshot(root) {
  const files = [];
  async function visit(directory) {
    for (const entry of (await readdir(directory)).sort()) {
      const path = join(directory, entry);
      const stat = await lstat(path, { bigint: true });
      assert(!stat.isSymbolicLink(), `Unexpected evidence symlink: ${path}`);
      if (stat.isDirectory()) {
        await visit(path);
      } else {
        assert(stat.isFile() && stat.size <= 128n * 1024n * 1024n,
          `Unexpected/oversized evidence file: ${path}`);
        assert(files.length < 128, 'Evidence file-count budget exceeded');
        files.push({
          reference: relative(root, path),
          byteLength: Number(stat.size),
          sha256: sha256(await readFile(path)),
          mtimeNs: String(stat.mtimeNs),
        });
      }
    }
  }
  await visit(root);
  return files;
}

// Every invocation starts a Linux process group, including its Python descendants.
async function command(script, args, environment, cwd, deadline, logBase) {
  const remaining = deadline - Date.now();
  if (remaining <= 0) {
    throw Object.assign(new Error('Case time budget exhausted before invocation'), { blocked: true });
  }
  return new Promise((fulfill, reject) => {
    const startedAt = new Date().toISOString();
    const child = spawn(process.execPath, [script, ...args], {
      cwd, env: environment, detached: true, stdio: ['ignore', 'pipe', 'pipe'],
    });
    const chunks = { stdout: [], stderr: [] };
    const sizes = { stdout: 0, stderr: 0 };
    let timedOut = false;
    let outputLimitExceeded = false;
    let spawnError = null;
    let killTimer;
    const killGroup = (signal) => {
      if (child.pid === undefined) return;
      try { process.kill(-child.pid, signal); } catch (error) {
        if (error.code !== 'ESRCH') spawnError ??= errorRecord(error);
      }
    };
    const terminate = () => {
      killGroup('SIGTERM');
      killTimer ??= setTimeout(() => killGroup('SIGKILL'), 2_000);
    };
    const timer = setTimeout(() => { timedOut = true; terminate(); }, remaining);
    for (const name of ['stdout', 'stderr']) {
      child[name].on('data', (chunk) => {
        const space = Math.max(0, OUTPUT_BYTES - sizes[name]);
        if (space > 0) chunks[name].push(chunk.subarray(0, space));
        sizes[name] += chunk.length;
        if (sizes[name] > OUTPUT_BYTES && !outputLimitExceeded) {
          outputLimitExceeded = true;
          terminate();
        }
      });
    }
    child.once('error', (error) => { spawnError = errorRecord(error); });
    child.once('close', async (exitCode, terminationSignal) => {
      clearTimeout(timer);
      clearTimeout(killTimer);
      if (timedOut || outputLimitExceeded) killGroup('SIGKILL');
      const stdout = Buffer.concat(chunks.stdout);
      const stderr = Buffer.concat(chunks.stderr);
      const receipt = {
        command: [process.execPath, script, ...args],
        cwd, pid: child.pid ?? null, startedAt, endedAt: new Date().toISOString(),
        exitCode, terminationSignal, timedOut, outputLimitExceeded, spawnError,
        stdoutBytes: sizes.stdout, stderrBytes: sizes.stderr,
        stdoutPath: `${logBase}.stdout`, stderrPath: `${logBase}.stderr`,
      };
      try {
        await writeFile(receipt.stdoutPath, stdout, { flag: 'wx' });
        await writeFile(receipt.stderrPath, stderr, { flag: 'wx' });
        await save(`${logBase}.process.json`, receipt);
        fulfill({ receipt, stdout, stderr });
      } catch (error) { reject(error); }
    });
  });
}

async function nativeCase(id, caseRoot) {
  assert(CASES.slice(4, RUNTIME_CASE_COUNT).some((item) => item.id === id), 'Unknown native case');
  assert.equal(caseRoot, join(RESULTS, id));
  const abort = new AbortController();
  for (const signal of ['SIGINT', 'SIGTERM']) {
    process.once(signal, () => abort.abort());
  }
  const verifier = await import(pathToFileURL(PINS[3][0]).href);
  const verified = await verifier.verifyFlowBlindHiddenFlowPackage(abort.signal);
  assert.strictEqual(
    verifier.flowBlindHiddenFlowPackageRuntimeAuthority(verified.runtimeAuthority),
    verified.runtimeAuthority,
    'Use only a live authority from the original verifier module',
  );
  assert.equal(verified.runtimePath, PINS[4][0]);
  assert.equal(verified.lock.publicRuntime.sha256, PINS[4][1]);
  assert.equal(verifier.expectedLockIdentity.sha256, PINS[5][1]);
  const runtime = await import(
    `${pathToFileURL(verified.runtimePath).href}?sha256=${verified.lock.publicRuntime.sha256}`
  );
  assert.equal(runtime.catalogHiddenFlowRuntimeVersion, '1.1.0');
  assert.equal(typeof runtime.callCatalogHiddenFlowAction, 'function');
  await save(join(caseRoot, 'source-authority.json'), {
    sourceCommit: SOURCE, originalVerifier: PINS[3][0], runtimePath: verified.runtimePath,
    originalAuthorityAccepted: true, packageVerification: verified.packageVerification,
    runtimeExports: Object.keys(runtime).sort(), coverageGaps: COVERAGE_GAPS,
  });
  const human = { goal: GOAL, details: null };
  const problemBytes = await readFile(join(caseRoot, 'selected-input/problem.json'));
  assert.equal(problemBytes.toString('utf8'),
    runtime.serializeCatalogHiddenFlowValue(JSON.parse(problemBytes)),
    'Fixture serialization must match the original attested stable-JSON serializer');
  const prepared = await runtime.callCatalogHiddenFlowAction('prepare-study', human, {
    selectedProblemBytes: problemBytes, signal: abort.signal,
  }, verified.runtimeAuthority);
  const preparationResponseBytes = runtime.serializeCatalogHiddenFlowValue(prepared.response);
  await writeFile(join(caseRoot, 'native-preparation-response.json'), preparationResponseBytes,
    { flag: 'wx' });
  if (prepared.response.status !== 'prepared-awaiting-confirmation') {
    process.stdout.write(preparationResponseBytes);
  }
  assert.equal(prepared.response.status, 'prepared-awaiting-confirmation',
    'Small fixture must be accepted before its scientific outcome is interpreted');
  assert.equal(prepared.response.containsResults, false);
  assert.equal(prepared.response.problem.retainedBasisFunctions, 1);
  assert.equal(prepared.response.problem.solverRequestCount,
    id === 'PR163-HID-MIXED-POINT-SPEED' ? 4 : 1);
  assert(prepared.bundle?.bytes instanceof Uint8Array);
  await writeFile(join(caseRoot, 'preparation-bundle.json'), prepared.bundle.bytes, { flag: 'wx' });
  const run = await runtime.callCatalogHiddenFlowAction('run-and-verify-study', human, {
    preparationBundleBytes: prepared.bundle.bytes, signal: abort.signal,
  }, verified.runtimeAuthority);
  const responseBytes = runtime.serializeCatalogHiddenFlowValue(run.response);
  await writeFile(join(caseRoot, 'native-run-response.json'), responseBytes, { flag: 'wx' });
  if (run.artifacts !== null && run.artifacts !== undefined) {
    await writeFile(join(caseRoot, 'published/verified-run.json'),
      run.artifacts.json.bytes, { flag: 'wx' });
    await writeFile(join(caseRoot, 'published/report.md'),
      run.artifacts.markdown.bytes, { flag: 'wx' });
  }
  process.stdout.write(responseBytes);
}

async function main() {
  // An accidental invocation on the review host fails before reading/importing PR code.
  assert.equal(process.platform, 'linux', 'Isolated Linux CI only');
  await mkdir(RESULTS, { recursive: true });
  const receipts = CASES.map(({ id, requirement, expected }) => ({
    id, requirement, expected, status: 'Blocked',
    observed: { sourceCommit: SOURCE, execution: 'Not started; no runtime evidence yet' },
  }));
  await save(join(RESULTS, 'receipts.json'), receipts);
  const startedAt = new Date().toISOString();
  const deadline = Date.now() + COLLECTION_MS;
  const workRoot = resolve('/work', `pr163-hidden-probes-${process.pid}`);
  let workCreated = false;
  try {
    assert.equal(process.arch, 'x64', 'Original Linux amd64 image required');
    assert.equal(process.versions.node, '22.22.0', 'Original pinned Node version required');
    assert.equal(process.getuid(), 10001, 'Nonroot uid 10001 required');
    await mkdir(workRoot);
    workCreated = true;
    await save(join(RESULTS, 'plan.json'), {
      sourceCommit: SOURCE, startedAt, cases: CASES, coverageGaps: COVERAGE_GAPS,
      command: 'node /harness/pr163-hidden-probes.mjs',
      evidenceSource: 'New isolated fork-only harness; receipts reflect this execution only',
      protocolRepairs: {
        priorRun: '35940651499',
        priorEvidenceArchiveSha256: '89b97d2fe6b0573bb9b1cdd6693e53ff262e989ff7aab12150aad538749221d8',
        fixtureChange: 'Canonical key ordering only; scientific inputs and expected outcomes unchanged. Prior native cases stopped at problem-not-canonical before solving.',
        restartChange: 'Select only the two unchanged preparation files under their original content-addressed directory, not the five-file published output tree.',
        priorEvidence: 'Initial receipts and raw outputs retained unchanged; not relabeled as PR defects or replaced by this run.',
      },
      observedEnvironment: {
        node: process.versions.node, platform: process.platform,
        architecture: process.arch, uid: process.getuid(),
        pythonIdentitySource: 'Actual native solver evidence when exposed; never inferred from a successful image build',
      },
      expectedEnvironment: {
        node: '22.22.0', python: '3.12.14', architecture: 'linux/amd64',
        uid: 10001, cpus: 2, memoryGiB: 3, pids: 256, network: 'none',
        perCaseMilliseconds: CASE_MS, terminationGraceMilliseconds: 2_000,
        collectionMilliseconds: COLLECTION_MS, outerProcessLimitSeconds: 240,
        finalEvidenceReserveMilliseconds: 30_000,
      },
      safety: {
        change: 'Fixtures/evidence in /results/hidden and harness-owned inputs in /work only',
        blastRadius: 'Disposable, secret-free fork CI; original readonly runtime; no Azure, LLM, production or upstream changes',
        containmentOwner: 'Fork CI operator owns container limits, no-network/no-token isolation and container teardown',
      },
      provenanceBoundary: 'These exact inspected file pins are checked here; CI must establish the full 399-file source tree and both original Dockerfile/image build provenance.',
      expectedPins: PINS.map(([path, hash]) => ({ path, sha256: hash })),
    });
    const checkedPins = [];
    for (const [path, expected] of PINS) {
      const bytes = await readFile(path);
      assert.equal(sha256(bytes), expected, `Pinned source mismatch: ${path}`);
      checkedPins.push({ path, sha256: expected, byteLength: bytes.length });
    }
    await save(join(RESULTS, 'checked-pins.json'), checkedPins);
    receipts[RUNTIME_CASE_COUNT].observed = {
      sourceCommit: SOURCE,
      execution: 'Not run',
      evidenceSource: 'Static inspection of the exact pinned shipped export surface',
      originalRuntime: PINS[4][0],
      originalRuntimeSha256: PINS[4][1],
      inaccessibleOperation: 'verifyHiddenFlowSupportResult / controlled solver-certificate injection',
      reason: 'The shipped runtime exports attested actions and response/report contract assertions, not the inner verifier. Its native action creates the original pinned solver internally. Direct certificate testing is Blocked, not covered by the end-to-end cases.',
      containment: 'No internal-code extraction, eval, solver replacement, or bundle mutation attempted.',
    };
    await save(join(RESULTS, 'receipts.json'), receipts, true);
    const activationBytes = await readFile(ACTIVATION);
    const activation = JSON.parse(activationBytes);
    assert.equal(activation.activationClass, 'package-local-retained-conformance');
    assert.equal(activation.liveActivationEligible, false);
    assert.equal(activation.advertiseToScientist, false);
    assert.equal(activation.enabled, true);
    assert.equal(activation.runtimeNetworkRequired, false);
    const activationCopy = join(RESULTS, 'activation-manifest.json');
    await writeFile(activationCopy, activationBytes, { flag: 'wx' });
    const baseBytes = await readFile(EXAMPLE);
    const base = JSON.parse(baseBytes);
    const scenario = await import(pathToFileURL(SCENARIO).href);
    assert.equal(typeof scenario.deriveHiddenScenarioProblemBytes, 'function');

    for (let index = 0; index < RUNTIME_CASE_COUNT; index += 1) {
      const definition = CASES[index];
      const receipt = receipts[index];
      const caseRoot = join(RESULTS, definition.id);
      const outputRoot = join(caseRoot, 'published');
      const caseWork = join(workRoot, definition.id);
      const caseDeadline = Math.min(deadline, Date.now() + CASE_MS);
      let outputCreated = false;
      let response = null;
      let selectedBytes;
      receipt.observed = {
        sourceCommit: SOURCE, startedAt: new Date().toISOString(),
        execution: 'Started', invocations: [], coverageGaps: COVERAGE_GAPS,
      };
      await save(join(RESULTS, 'receipts.json'), receipts, true);
      try {
        await mkdir(caseRoot);
        await mkdir(outputRoot);
        outputCreated = true;
        await mkdir(caseWork);
        await mkdir(join(caseRoot, 'base-input'));
        await mkdir(join(caseRoot, 'selected-input'));
        await writeFile(join(caseRoot, 'base-input/problem.json'), baseBytes, { flag: 'wx' });
        if (index < 4) {
          selectedBytes = scenario.deriveHiddenScenarioProblemBytes(definition.id, baseBytes);
        } else {
          const problem = structuredClone(base);
          problem.id = definition.id.toLowerCase();
          problem.title = definition.requirement;
          problem.description = 'Generated single-basis, package-local PR163 counterexample; not experimental measurements.';
          problem.domain = {
            id: 'pr163-square', coordinateFrameId: 'pr163-cartesian', lengthUnit: 'mm',
            xEdges: [-4, -3, -2, -1, 0, 1, 2, 3, 4],
            yEdges: [-4, -3, -2, -1, 0, 1, 2, 3, 4],
            validCellMask: Array(64).fill(true),
          };
          problem.basis.centers = [{ id: 'only-basis', point: { x: 0, y: 0 } }];
          problem.observations = [];
          problem.budgets = { velocityRms: null, velocityGradientRms: null };
          problem.target = {
            id: 'pr163-target', title: 'Single-basis target at (0.5, 0)',
            point: { x: 0.5, y: 0 }, unit: 'mm/s',
            ...(index === 4
              ? { type: 'point-speed-envelope', directionCount: 4 }
              : { type: 'point-component', component: 'y' }),
          };
          if (index === 5) {
            problem.observations = [{
              id: 'impossible-zero-row', sensorId: 'generated-sensor',
              type: 'point-component', component: 'x', point: { x: 0, y: 0 },
              coordinateFrameId: 'pr163-cartesian', unit: 'mm/s',
              baselineValue: 0, observedValue: 1, tolerance: 0,
              provenance: 'Generated impossible equality 0*c=1; both velocity components vanish at the cubic center.',
            }];
          }
          problem.provenance = {
            sourceKind: 'generated', license: 'CC0-1.0',
            citation: 'New analytic PR163 fixture, derived from the attested bundled example schema.',
            transformations: ['Replaced domain, centers, observations, budgets and target; retained original basis/numerical policy.'],
          };
          problem.limitations = [
            'Synthetic one-coefficient finite-basis problem; not a physical-flow or continuous-domain quadrature-accuracy claim.',
          ];
          selectedBytes = canonicalProblemBytes(problem);
          await save(join(caseRoot, 'analytic-oracle.json'), {
            basis: 'tensor-cardinal-cubic-streamfunction-v1, center=(0,0), spacing=(1,1)',
            derivation: 'B(0)=2/3, B_prime(0)=0, B_prime(0.5)=-5/8. Before positive normalization, velocity(0.5,0)=(0,5/12); velocity(0,0)=(0,0).',
            implication: index === 4
              ? 'All real coefficients are feasible; speed is |c| times a positive constant. The zero x-support directions do not make the speed bounded.'
              : 'The sole observation is 0*c=1, so no coefficient is feasible. An improving homogeneous ray alone cannot establish unboundedness of this empty feasible set.',
            tolerances: problem.numerics,
          });
        }
        await writeFile(join(caseRoot, 'selected-input/problem.json'), selectedBytes, { flag: 'wx' });
        receipt.observed.fixtureSha256 = sha256(selectedBytes);
        const environment = {
          PATH: '/usr/local/bin:/usr/bin:/bin', HOME: caseWork, TMPDIR: caseWork,
          LANG: 'C.UTF-8', LC_ALL: 'C.UTF-8', TZ: 'UTC', PYTHONDONTWRITEBYTECODE: '1',
          FLOWBLIND_TOOL_ROLE: 'run-and-verify-study',
          FLOWBLIND_RESEARCH_GOAL: GOAL,
          FLOWBLIND_SCENARIO_ID: definition.id,
          FLOWBLIND_INPUT_ROOT: join(caseRoot, 'base-input'),
          FLOWBLIND_OUTPUT_ROOT: outputRoot,
          FLOWBLIND_PRIVATE_ACTIVATION_MANIFEST_PATH: activationCopy,
          FLOWBLIND_PRIVATE_ACTIVATION_MANIFEST_SHA256: `sha256:${sha256(activationBytes)}`,
        };
        const invoke = async (script, args, label, extra = {}) => {
          const run = await command(script, args, { ...environment, ...extra },
            caseWork, caseDeadline, join(caseRoot, label));
          receipt.observed.invocations.push(run.receipt);
          if (run.receipt.timedOut || run.receipt.outputLimitExceeded || run.receipt.spawnError) {
            throw Object.assign(new Error(`Invocation blocked; see ${label}.process.json`), { blocked: true });
          }
          if (run.receipt.exitCode !== 0 && run.stdout.length > 0) {
            try {
              receipt.observed.failureResponse = {
                invocation: label, value: JSON.parse(run.stdout.toString('utf8')),
              };
            } catch {
              // Non-JSON errors remain in the unchanged raw stdout/stderr artifacts.
            }
          }
          assert.equal(run.receipt.terminationSignal, null, `${label} terminated by signal`);
          assert.equal(run.receipt.exitCode, 0, `${label} failed; see exact stdout/stderr`);
          return run.stdout;
        };
        response = JSON.parse(await invoke(
          index < 4 ? SCENARIO : SELF,
          index < 4 ? ['run-hidden'] : ['--native-case', definition.id, caseRoot],
          index < 4 ? 'original-harness' : 'native-api',
        ));
        receipt.observed.response = response;
        const outcome = response.outcome;
        assert(outcome && typeof outcome === 'object', 'Missing actual outcome');
        if (index === 5) {
          assert.notEqual(outcome.evidenceState, 'verified-unbounded-within-tolerance');
          assert.notEqual(outcome.reason, 'no-finite-bound');
          if (response.status === 'unavailable' && outcome.kind === 'unavailable') {
            assert.equal(response.containsResults, false);
            if (!['verification-not-accepted', 'deterministic-replay-mismatch'].includes(outcome.reason)) {
              throw Object.assign(new Error(`No scientific verdict: ${outcome.reason}`), { blocked: true });
            }
            receipt.observed.scientificConclusion = 'INCONCLUSIVE, accepted fail-closed outcome; infeasibility was not certified.';
          } else {
            assert.equal(response.status, 'report-complete');
            assert.equal(response.containsResults, true);
            assert.equal(outcome.kind, 'infeasible');
            assert.equal(outcome.evidenceState, 'verified-infeasible-within-tolerance');
          }
        } else {
          assert.equal(response.status, 'report-complete');
          if (index === 1) {
            assert.equal(response.containsResults, true);
            assert.equal(outcome.kind, 'infeasible');
            assert.equal(outcome.evidenceState, 'verified-infeasible-within-tolerance');
          } else if (index === 2 || index === 4) {
            assert.equal(response.containsResults, false);
            assert.equal(outcome.kind, 'unavailable');
            assert.equal(outcome.evidenceState, 'verified-unbounded-within-tolerance');
            assert.equal(outcome.reason, 'no-finite-bound');
          } else {
            assert.equal(response.containsResults, true);
            assert.equal(outcome.kind, 'numerical');
            assert(Number.isFinite(outcome.value));
            assert.equal(outcome.unit, 'mm/s');
          }
        }
        if (index === 3) {
          const before = await snapshot(outputRoot);
          await save(join(caseRoot, 'before-restart-files.json'), before);
          const firstRead = await invoke(join(RUNTIME, 'flowblind-report-resource-v2.mjs'),
            [response.resource.uri], 'cold-read-1');
          assert.equal(firstRead.length, response.resource.byteLength);
          assert.equal(sha256(firstRead), response.resource.sha256);
          const { bundleSha256, markerSha256 } = response.preparation;
          assert.match(bundleSha256, /^[a-f0-9]{64}$/u);
          assert.match(markerSha256, /^[a-f0-9]{64}$/u);
          const preparationDirectory = `flowblind-hidden-flow-preparation-${bundleSha256}`;
          const restartInputRoot = join(caseRoot, 'restart-input');
          await mkdir(join(restartInputRoot, preparationDirectory), { recursive: true });
          // The selector requires these relative paths and exactly two files, not the report set.
          for (const [name, expectedHash] of [
            ['preparation.json', bundleSha256], ['preparation-commit.json', markerSha256],
          ]) {
            const bytes = await readFile(join(outputRoot, preparationDirectory, name));
            assert.equal(sha256(bytes), expectedHash, 'Restart must reuse exact preparation bytes');
            await writeFile(join(restartInputRoot, preparationDirectory, name), bytes, { flag: 'wx' });
          }
          receipt.observed.restartInputRoot = restartInputRoot;
          receipt.observed.restartSelection = await snapshot(restartInputRoot);
          const restarted = JSON.parse(await invoke(
            join(RUNTIME, 'flowblind-run-and-verify-study-v2.mjs'), [], 'cold-restart',
            { FLOWBLIND_INPUT_ROOT: restartInputRoot },
          ));
          receipt.observed.restartResponse = restarted;
          assert.equal(restarted.status, 'report-complete');
          assert.equal(restarted.containsResults, response.containsResults);
          assert.deepEqual(restarted.result?.outcome, outcome);
          assert.equal(restarted.publication?.resourceUri, response.resource.uri);
          const secondRead = await invoke(join(RUNTIME, 'flowblind-report-resource-v2.mjs'),
            [restarted.publication.resourceUri], 'cold-read-2');
          assert(firstRead.equals(secondRead), 'Cold resource bytes changed across restart');
          const after = await snapshot(outputRoot);
          await save(join(caseRoot, 'after-restart-files.json'), after);
          assert.deepEqual(after, before, 'Restart changed committed files/markers/mtimes');
          receipt.observed.coldResourceSha256 = sha256(secondRead);
        }
        receipt.status = 'Passed';
      } catch (error) {
        receipt.status = error.blocked ? 'Blocked' : 'Failed';
        receipt.error = errorRecord(error);
      } finally {
        try {
          if (outputCreated) {
            const files = await snapshot(outputRoot);
            await save(join(caseRoot, 'published-files.json'), files);
            const nativeFiles = files.filter((file) => basename(file.reference) === 'verified-run.json');
            receipt.observed.nativeStatuses = [];
            for (const file of nativeFiles) {
              const native = JSON.parse(await readFile(join(outputRoot, file.reference)));
              const support = native.evidence?.result?.support;
              for (const part of support?.directionalResults ?? (support ? [support] : [])) {
                receipt.observed.nativeStatuses.push({
                  evidence: join(outputRoot, file.reference),
                  requestId: part.request?.requestId ?? null,
                  status: part.solverResult?.solution?.status ?? null,
                  verification: part.verification ?? null,
                  solver: part.solverResult?.solver ?? null,
                });
              }
              if (receipt.status === 'Passed') {
                assert.equal(native.recordType, 'flowblind-catalog-hidden-flow-verified-run-v1');
                assert.deepEqual(native.outcome, response.outcome);
                assert.equal(native.deterministicReplay?.matched, true);
                assert.deepEqual(native.evidence.problem, JSON.parse(selectedBytes),
                  'Published native evidence must bind the exact selected scientific fixture');
              }
            }
            if (receipt.status === 'Passed' && response?.status === 'report-complete') {
              assert.equal(nativeFiles.length, 1, 'Expected one original native verified-run artifact');
              assert(receipt.observed.nativeStatuses.length > 0);
              assert(receipt.observed.nativeStatuses.every((item) => typeof item.status === 'string'),
                'Report actual native status, not the projected outcome kind');
            }
            receipt.observed.nativeStatusExposure = receipt.observed.nativeStatuses.length > 0
              ? 'Actual statuses from retained native evidence; original bytes preserved.'
              : 'Not exposed by this response. Native unavailable results discard inner support/certificate data; no status inferred.';
          }
        } catch (error) {
          receipt.observed.evidenceCollectionError = errorRecord(error);
          if (receipt.status === 'Passed') {
            receipt.status = 'Failed';
            receipt.error = errorRecord(error);
          }
        }
        receipt.observed.execution = receipt.observed.invocations.length === 0
          ? 'Not run' : 'Invoked; see exact process outcomes';
        receipt.observed.endedAt = new Date().toISOString();
        if (index >= 4) {
          try {
            const authorityPath = join(caseRoot, 'source-authority.json');
            const authority = JSON.parse(await readFile(authorityPath));
            const gap = receipts[RUNTIME_CASE_COUNT];
            gap.observed.runtimeExportEvidence = authorityPath;
            gap.observed.actualRuntimeExports = authority.runtimeExports;
            gap.observed.originalAuthorityAccepted = authority.originalAuthorityAccepted;
            gap.observed.evidenceSource = 'Pinned source inspection plus actual original-verifier authority and imported module exports in isolated CI';
          } catch (error) {
            receipts[RUNTIME_CASE_COUNT].observed.runtimeExportEvidenceUnavailable = errorRecord(error);
          }
        }
        // Only this invocation's scratch inputs are removable; all evidence is retained.
        try { await rm(caseWork, { recursive: true, force: true }); } catch (error) {
          receipt.observed.cleanupError = errorRecord(error);
          if (receipt.status === 'Passed') receipt.status = 'Blocked';
        }
        await save(join(RESULTS, 'receipts.json'), receipts, true);
      }
    }
    for (const [path, expected] of PINS) {
      assert.equal(sha256(await readFile(path)), expected, `Pinned package bytes changed: ${path}`);
    }
    await save(join(RESULTS, 'source-pins-after.json'), {
      checkedAt: new Date().toISOString(), allInspectedPinsUnchanged: true, checkedPins,
    });
  } catch (error) {
    for (const receipt of receipts) {
      if (receipt.status === 'Passed') receipt.status = 'Blocked';
      receipt.observed.collectionError = errorRecord(error);
    }
    await save(join(RESULTS, 'collection-error.json'), errorRecord(error));
  } finally {
    if (workCreated) {
      try { await rm(workRoot, { recursive: true, force: true }); } catch (error) {
        for (const receipt of receipts) {
          if (receipt.status === 'Passed') receipt.status = 'Blocked';
          receipt.observed.cleanupError = errorRecord(error);
        }
      }
    }
    await save(join(RESULTS, 'receipts.json'), receipts, true);
    process.exitCode = receipts.some((receipt) => receipt.status !== 'Passed') ? 1 : 0;
    process.stdout.write(`${JSON.stringify(receipts.map(({ id, status }) => ({ id, status })))}\n`);
  }
}

try {
  assert.equal(process.platform, 'linux', 'Do not run/import PR source on the review host');
  if (process.argv[2] === '--native-case') {
    await nativeCase(process.argv[3], process.argv[4]);
  } else {
    assert.equal(process.argv.length, 2, 'Use node /harness/pr163-hidden-probes.mjs');
    await main();
  }
} catch (error) {
  // Includes the actual thrown worker error; unavailable API responses are not rewritten.
  process.stderr.write(`${JSON.stringify(errorRecord(error), null, 2)}\n`);
  process.exitCode = 1;
}
