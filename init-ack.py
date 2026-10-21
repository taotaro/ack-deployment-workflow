#!/usr/bin/env python3
"""ACK bootstrap: plan, validate in temp storage, confirm, write files only.
VPS-pull remains owned by the existing init.sh path. No Git/network mutation.
"""
import argparse
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
import yaml

HOME = Path(__file__).resolve().parent

def fail(message):
    raise SystemExit(message)

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--strategy', choices=['ack'], default='ack')
    for name in ['app', 'customer', 'product', 'module', 'port', 'health', 'environment-map', 'central-sha']:
        p.add_argument('--' + name)
    p.add_argument('--config-map', default='')
    p.add_argument('--service', action='store_true', help='Explicitly include an HTTP Service')
    p.add_argument('--hpa', action='store_true', help='Explicitly include an HPA')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--yes', action='store_true')
    args = p.parse_args()
    root = Path.cwd().resolve()
    git = subprocess.run(['git', 'rev-parse', '--show-toplevel'], capture_output=True, text=True)
    if git.returncode or Path(git.stdout.strip()).resolve() != root:
        fail('Run at an existing Git repository root')
    if not (root / 'Dockerfile').is_file():
        fail('An existing root Dockerfile is required')
    for key in ['app','customer','product','module','port','health','environment_map','central_sha']:
        if not getattr(args, key):
            setattr(args, key, input(key.replace('_', '-') + ': ').strip())
    for key in ['app','customer','product','module']:
        value = getattr(args,key)
        if not re.fullmatch(r'[a-z0-9]([-a-z0-9]*[a-z0-9])?',value) or len(value)>63:
            fail('Invalid DNS label: '+key)
    if args.app != '-'.join([args.customer,args.product,args.module]):
        fail('app must equal the explicitly confirmed customer-product-module name')
    if not args.port.isdigit() or not 1 <= int(args.port) <= 65535:
        fail('Invalid application port')
    if not re.fullmatch(r'/[^\s]*',args.health):
        fail('Invalid health path')
    if not re.fullmatch(r'[0-9a-f]{40}', args.central_sha) or args.central_sha == '0'*40:
        fail('An authorized published full central commit SHA is required')
    if args.config_map and not re.fullmatch(r'[a-z0-9]([-a-z0-9]*[a-z0-9])?',args.config_map):
        fail('Invalid ConfigMap name')
    mapping = json.loads(args.environment_map)
    if not isinstance(mapping,dict) or not mapping:
        fail('environment-map must explicitly map branch to overlay and environment')
    for branch, value in mapping.items():
        if not re.fullmatch(r'[A-Za-z0-9_.-]+',branch):
            fail('Use a branch with an explicit branch-env Environment name')
        if not isinstance(value,dict) or set(value) != {'overlay','environment'}:
            fail('Each branch needs exactly overlay and environment')
        for key in value:
            if not re.fullmatch(r'[a-z0-9]([-a-z0-9]*[a-z0-9])?',value[key]):
                fail('Invalid mapping '+key)
        if value['environment'] not in ('prod', 'prd', 'production', 'dev', 'development', 'sit', 'uat', 'preview'):
            fail('Unknown routing environment: ' + value['environment'])
    # No automatic push trigger. Branch and Environment remain explicit at dispatch.
    caller = yaml.safe_load((HOME/'templates/ack/caller.yaml.tmpl').read_text().replace('__CENTRAL_SHA__',args.central_sha))
    branches = json.dumps(list(mapping), separators=(',', ':'))
    caller['jobs']['build']['if'] = "${{ contains(fromJSON('" + branches + "'), github.ref_name) }}"
    files = {'.github/workflows/deploy.yaml':yaml.safe_dump(caller,sort_keys=False)}
    for src in (HOME/'templates/ack/single-app/base').glob('*.yaml'):
        if src.name == 'hpa.yaml' and not args.hpa:
            continue
        content = src.read_text()
        if src.name == 'kustomization.yaml' and not args.hpa:
            content = content.replace('  - hpa.yaml\n','')
        if src.name == 'deployment.yaml':
            if not args.config_map:
                content = content.replace('        envFrom:\n        - configMapRef:\n            name: __CONFIG_MAP_NAME__\n            optional: true\n','')
            if args.service:
                content += '\n---\napiVersion: v1\nkind: Service\nmetadata:\n  name: __APP_NAME__\nspec:\n  selector:\n    app: __APP_NAME__\n  ports:\n  - port: __SERVICE_PORT__\n    targetPort: __APP_PORT__\n'
        files['deploy/base/'+src.name] = content
    for value in mapping.values():
        files['deploy/overlays/'+value['overlay']+'/kustomization.yaml'] = 'apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\nresources:\n- ../../base\n'
    docs = ['# ACK deployment setup', '', 'Generated files do not configure GitHub or cloud resources and do not deploy.', '',
            '## Confirmed application', f'CUSTOMER_NAME={args.customer}', f'PRODUCT_NAME={args.product}', f'MODULE_NAME={args.module}',
            f'APP_PORT={args.port}', f'SERVICE_PORT={args.port}', f'HEALTHCHECK_PATH={args.health}', f'CONFIG_MAP_NAME={args.config_map}', '',
            '## Explicit branch / Environment mapping', '| Branch | GitHub Environment | DEPLOY_OVERLAY | ENVIRONMENT |', '|---|---|---|---|']
    docs += [f'| {b} | {b}-env | {v["overlay"]} | {v["environment"]} |' for b,v in mapping.items()]
    docs += ['', 'Set these application variables in each listed Environment; no values are inferred from Dockerfile.',
             'Configure ACR_NAMESPACE/ACR_REPOSITORY, required app variables above and optional resource overrides.',
             'Authorize the repository for Org LUMIO_PROD_* and LUMIO_NONPROD_* target bundles and private workflow access.',
             'Each selected bundle needs REGION_ID, ACR_REGISTRY, ACR_INSTANCE_ID, ACK_CLUSTER_ID Variables and ALICLOUD_ACCESS_KEY_ID/ALICLOUD_ACCESS_KEY_SECRET Secrets.',
             'Optional full Environment override: set all six LUMIO_TARGET_* members; a partial override fails, never mixes credentials with defaults.',
             'All six cloud Secrets are explicitly transmitted by name, even when only defined in the actual Environment.',
             'The unused default bundle may be empty. Keep TARGET absent at Org/repository scope; GitHub merges same-name keys individually.',
             'Target selection is not credential isolation; Org authorization is repository-level.',
             'Confirm real Secret/Environment precedence in a separately authorized GitHub execution; generation is local proof only.',
             'Set application build Secrets and LARK_BOT_WEBHOOK explicitly; do not store their values in files.',
             'Confirm target binding, Environment approval rules, existing Namespace/ServiceAccount and any ConfigMap.',
             'Dispatch only from a mapped branch after review and separately authorized first deployment verification.',
             'No Ingress/DNS/cloud resources are created. Rollout undo only reverts Deployment revisions, not all resources.',
             'No previous revision may exist on first deployment. A central workflow version rollback is a separate operation.', '']
    files['docs/deployment.md'] = '\n'.join(docs)
    # Resolve all destination paths before any writes. Existing assets are never overwritten.
    for name in files:
        dest=root/name
        if dest.exists() or dest.is_symlink() or not dest.resolve().is_relative_to(root):
            fail('Refusing existing or escaping path: '+name)
    with tempfile.TemporaryDirectory() as temp:
        stage=Path(temp)
        for name,content in files.items():
            dest=stage/name; dest.parent.mkdir(parents=True,exist_ok=True); dest.write_text(content)
            if dest.suffix == '.yaml': list(yaml.safe_load_all(content))
        if shutil.which('kustomize'):
            for value in mapping.values():
                subprocess.run(['kustomize','build',str(stage/'deploy/overlays'/value['overlay'])],check=True,capture_output=True)
        print('Planned files:\n'+'\n'.join(sorted(files)))
        if args.dry_run:
            print('Dry-run: no target files, branch, index, commit or network changes.')
            return
        if not args.yes and input('Write these files only? [y/N]: ').strip().lower() not in ('y','yes'):
            fail('Cancelled')
        for name in files:
            dest=root/name; dest.parent.mkdir(parents=True,exist_ok=True)
            with dest.open('x') as output:
                output.write((stage/name).read_text())
    print('Files written only. No Git or platform changes.')

if __name__ == '__main__':
    main()
