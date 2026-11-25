"""Offline validation for this draft family in a trusted Discovery checkout.

Always run the catalog's scoped structural, documentation and schema checks.
With --cache-dir, reuse only exact-input successful local CPU test receipts.
This is a one-shot command, not an installer, scheduler, cloud or PR validator.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import time
from importlib.metadata import version

import yaml

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / '.github' / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from catalog_validation.documentation import check_documentation
from catalog_validation.schema_checks import check_schema
from catalog_validation.schemas import CatalogSchemas
from catalog_validation.structural import check_structural

NAMES = tuple('quantum-' + op for op in (
    'active-space', 'hamiltonian', 'ground-state', 'dynamics', 'noise',
    'entanglement', 'many-body', 'error-correction', 'resources', 'spectroscopy'))
BASE_TESTS = {'test_reference', 'test_boundary', 'test_invalid', 'test_wrong_operation'}


def digest_files(paths):
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.relative_to(ROOT).as_posix().encode() + b'\0')
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def check_layout(folder):
    def load(name):
        return yaml.safe_load((folder / name).read_text(encoding='utf-8'))
    slug = folder.name
    tool = load(f'tools/{slug}/tool.yaml')
    ext = load('agent.yaml')['discoveryExtensions']
    action, = tool['actions']
    infra, = tool['infra']
    binding, = ext['tools']
    expect = json.loads((folder/'tools'/slug/'example.json').read_text(encoding='utf-8'))
    checks = {
        'owned tool path': load('metadata.yaml')['associated_tools'] == [f'agents/{slug}/tools/{slug}'],
        'approved binding': binding == {'toolId':'{{'+tool['name']+'ToolId}}','confirmation':'Enabled'} and ext['humanInTheLoop']=='Enabled',
        'one contracted operation': action['name'] == expect['operation'] == slug.removeprefix('quantum-').replace('-','_'),
        'no accelerator allocation': all(infra['compute'][key]['gpu']==0 for key in ('min_resources','max_resources')),
        'fixed input transport': action['inline_files']==[{'mount_path':'/input/request.json','content':'{{request_json}}'}]
                                 and action['command']=='--request /input/request.json --output /output',
        'output kept for review': action['output_mount_configurations'][0]['auto_promote'] is False,
    }
    failed = [k for k,v in checks.items() if not v]
    if failed:
        raise ValueError(f'{slug}: {failed}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--only', choices=NAMES, help='otherwise validate all ten packages')
    parser.add_argument('--cache-dir', type=Path, help='trusted local receipts outside the checkout')
    parser.add_argument('--fresh', action='store_true', help='ignore existing receipts')
    args = parser.parse_args()
    if args.cache_dir and args.cache_dir.resolve().is_relative_to(ROOT):
        parser.error('use a receipt directory outside the checkout')
    names = (args.only,) if args.only else NAMES
    folders = {Path('agents')/n for n in names}
    schemas = CatalogSchemas.load(ROOT)
    if schemas.config_errors():
        raise ValueError(schemas.config_errors())
    findings = (check_structural(ROOT, folders, []) + check_documentation(ROOT, folders) +
                check_schema(ROOT, folders, schemas.agent, schemas.tool, schemas.metadata, schemas.registry))
    if findings:
        for issue in findings:
            print(issue, file=sys.stderr)
        return 1

    common = digest_files([Path(__file__).resolve(), *(ROOT/'docs'/'schemas').glob('*.json'),
                           *(SCRIPTS/'catalog_validation').glob('*.py')])
    environment = {'python':sys.version, 'platform':platform.platform(), 'machine':platform.machine(),
                   'dependencies':{p:version(p) for p in ('numpy','PyYAML','jsonschema','referencing')},
                   'blas_threads':1}
    executed = reused = tests = 0
    for name in names:
        folder = ROOT/'agents'/name
        check_layout(folder)
        inputs = [p for p in folder.rglob('*') if p.is_file() and '__pycache__' not in p.parts and 'run-output' not in p.parts]
        key = hashlib.sha256(json.dumps([common,environment,digest_files(inputs)],sort_keys=True).encode()).hexdigest()
        receipt = args.cache_dir/name/(key+'.json') if args.cache_dir else None
        required = BASE_TESTS | ({'test_decision_survives_large_matrix'} if name=='quantum-hamiltonian' else set())
        cached = None
        if receipt and receipt.is_file() and not args.fresh:
            try:
                data = json.loads(receipt.read_text(encoding='utf-8'))
                if (isinstance(data,dict) and data.get('fingerprint')==key and data.get('status')=='passed'
                        and data.get('package')==name and isinstance(data.get('tests'),list)
                        and required.issubset(data['tests'])):
                    cached=data
            except (ValueError, TypeError, OSError):
                pass  # A corrupt receipt is a cache miss, never a pass.
        if cached:
            reused+=1; tests+=len(cached['tests'])
            print(f'{name}: unchanged; reused {len(cached["tests"])} tests')
            continue
        start=time.monotonic()
        proc = subprocess.run([sys.executable,'-B','-m','unittest','discover','-v'],
                              cwd=folder/'tools'/name, capture_output=True,text=True,timeout=90,
                              env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1'})
        log = proc.stdout + proc.stderr
        passed_names=re.findall(r'^(test_\w+) \([^\n]+\) \.\.\. ok\s*$',log,re.MULTILINE)
        ran=re.search(r'^Ran (\d+) tests? in ',log,re.MULTILINE)
        if (proc.returncode or not ran or not required.issubset(passed_names)
                or len(passed_names)!=int(ran.group(1)) or log.rstrip().splitlines()[-1]!='OK'):
            print(f'{name}: failed or incomplete; stopping (not cached).\n{log}', file=sys.stderr)
            return 1
        executed+=1; tests+=len(passed_names)
        if receipt:
            receipt.parent.mkdir(parents=True,exist_ok=True)
            data={'status':'passed','package':name,'fingerprint':key,'environment':environment,
                  'tests':passed_names,'elapsed_s':round(time.monotonic()-start,4)}
            tmp=receipt.with_suffix(f'.{os.getpid()}.tmp')
            tmp.write_text(json.dumps(data,indent=2)+'\n',encoding='utf-8'); tmp.replace(receipt)
        print(f'{name}: ran {len(passed_names)} tests')
    print(f'{len(names)} packages passed scoped catalog checks; executed={executed}, reused={reused}, tests accounted for={tests}')
    return 0


if __name__=='__main__':
    raise SystemExit(main())