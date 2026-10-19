#!/usr/bin/env python3
"""Synthetic-only local proof; no real Docker, kubectl, credentials or notifications."""
import copy
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
import yaml

CENTRAL=Path(__file__).resolve().parents[1]
IMPL=CENTRAL.parent
BASE=IMPL.parent/'centralized-ack-cicd-baseline'
WF='.github/workflows/build-image-to-acr-and-deploy-to-ack.yaml'
class Loader(yaml.SafeLoader): pass
Loader.yaml_implicit_resolvers=copy.deepcopy(Loader.yaml_implicit_resolvers)
for k,rules in Loader.yaml_implicit_resolvers.items():
    Loader.yaml_implicit_resolvers[k]=[(t,r) for t,r in rules if t!='tag:yaml.org,2002:bool']
def load(path): return yaml.load(path.read_text(),Loader=Loader)
W=load(CENTRAL/'.github/workflows/deploy-ack.yaml')
STEPS=W['jobs']['deploy']['steps']
def script(name): return next(s['run'] for s in STEPS if s.get('name')==name)
def execute(text,env,cwd):
    return subprocess.run(['bash','-eo','pipefail','-c',text],cwd=cwd,env=env,text=True,capture_output=True)
def apply_env(env,path):
    for line in path.read_text().splitlines():
        k,v=line.split('=',1);env[k]=v

class Proof(unittest.TestCase):
    def test_schema_shell_and_unchanged_boundaries(self):
        self.assertEqual(len(W['on']['workflow_call']['inputs']),26)
        self.assertEqual(sum(v.get('required') is True or v.get('required')=='true' for v in W['on']['workflow_call']['inputs'].values()),7)
        self.assertNotIn('concurrency',W['jobs']['deploy'])
        for repo,name in [('central','.github/workflows/deploy-ack.yaml'),('render',WF),('services',WF)]:
            data=load(IMPL/repo/name)
            for job in data['jobs'].values():
                for s in job.get('steps',[]):
                    if 'run' in s:
                        p=subprocess.run(['bash','-n'],input=s['run'],text=True,capture_output=True)
                        self.assertEqual(p.returncode,0,p.stderr)
        old=load(BASE/'services'/WF);new=load(IMPL/'services'/WF)
        for key in ['on','env','permissions','defaults','run-name']: self.assertEqual(old[key],new[key])
        for key in ['provision_e2e_runner','integration_login']: self.assertEqual(old['jobs'][key],new['jobs'][key])
        oldjob=old['jobs']['build_and_deploy'];newjob=new['jobs']['build_and_deploy']
        for key in ['needs','if','concurrency']:self.assertEqual(oldjob[key],newjob[key])
        self.assertEqual(old['jobs']['prepare']['steps'],new['jobs']['prepare']['steps'][:-1])
        self.assertEqual((BASE/'services/scripts/resolve_deploy_services.py').read_bytes(),(IMPL/'services/scripts/resolve_deploy_services.py').read_bytes())
        for repo in ['render','services']:
            self.assertEqual(load(BASE/repo/WF)['on'],load(IMPL/repo/WF)['on'])
        # VPS templates and functions outside the ACK dispatch routing stay byte-identical.
        original=IMPL.parents[1]/'repos/project-deployment-init'
        for p in (original/'templates/vps-pull').rglob('*'):
            if p.is_file():self.assertEqual(p.read_bytes(),(CENTRAL/p.relative_to(original)).read_bytes())
        old= (original/'init.sh').read_text().split('main() {')[0]
        self.assertEqual(old,(CENTRAL/'init.sh').read_text().split('main() {')[0])

    def test_build_argv_and_failure(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); docker=root/'docker'
            docker.write_text('#!/usr/bin/env python3\nimport os,json,sys\nopen(os.environ["ARGV"],"w").write(json.dumps(sys.argv[1:]))\nsys.exit(int(os.environ.get("DOCKER_FAIL","0")))\n');docker.chmod(0o755)
            env={'PATH':str(root)+os.pathsep+os.environ['PATH'],'ARGV':str(root/'argv'),'IMAGE_URL':'synthetic.example/app','IMAGE_TAG':'a'*40,'CONFIG_MAP_TOKEN':'synthetic value * [x]','CONFIG_MAP_EMPTY':''}
            p=execute(script('Build single application'),env,root)
            self.assertEqual(p.returncode,0,p.stderr)
            argv=json.loads((root/'argv').read_text())
            self.assertIn('TOKEN=synthetic value * [x]',argv);self.assertNotIn('EMPTY=',argv)
            self.assertIn('Added build argument: --build-arg TOKEN=synthetic value * [x]',p.stdout)
            env['DOCKER_FAIL']='7';self.assertEqual(execute(script('Build single application'),env,root).returncode,7)

    def test_matrix_actual_prepare(self):
        data=load(IMPL/'services'/WF)
        text=data['jobs']['prepare']['steps'][-1]['run']
        for names in [['task-worker','user-api'],['payment-api'],['user-api','business-api','payment-api','task-worker']]:
            with tempfile.TemporaryDirectory() as d:
                path=Path(d)/'out';path.touch()
                env=dict(os.environ,SERVICES=json.dumps(names),GITHUB_OUTPUT=str(path))
                p=execute(text,env,d);self.assertEqual(p.returncode,0,p.stderr)
                matrix=json.loads(path.read_text().split('=',1)[1])['include']
                self.assertEqual([r['service'] for r in matrix],names)
                self.assertEqual([r['app_port'] for r in matrix],['' if n=='task-worker' else {'user-api':'1001','business-api':'1002','payment-api':'1003'}[n] for n in names])

    def fixture(self,root,profile,target='dev',kind='rest_api'):
        env={k:v for k,v in os.environ.items() if not k.startswith('CONFIG_MAP_')}
        # Do not inherit cloud/application credential variables from the invoking shell.
        for k in list(env):
            if any(word in k for word in ('SECRET','TOKEN','MONGODB','ALICLOUD','LARK_BOT')):env.pop(k)
        for name,value in W['jobs']['deploy']['env'].items():
            env[name]='' if '${{' in str(value) else str(value)
        env.update(PROFILE=profile,REF_NAME='development',EVENT_NAME='push',IMAGE_TAG='a'*40,IMAGE_URL='synthetic.example/team/app',ACR_REGISTRY='synthetic.example',ACR_NAMESPACE='team',ACR_REPOSITORY='app',TARGET_ENV=target,CUSTOMER_NAME='fixture',PRODUCT_NAME='product',MODULE_NAME='app',DEPLOY_OVERLAY='development' if profile=='single-app' or target=='dev' else target,GITHUB_ENV=str(root/'env'),GITHUB_OUTPUT=str(root/'output'),ALICLOUD_ACCESS_KEY_ID='synthetic-ak',ALICLOUD_ACCESS_KEY_SECRET='synthetic-sk')
        values={'profile':profile,'github_environment':'development-env' if profile=='single-app' else target+'-env','region_id':'synthetic-region','acr_registry':'synthetic.example','acr_instance_id':'synthetic-instance','ack_cluster_id':'synthetic-cluster','notification_project_fallback':'synthetic label','target_env':target if profile=='service' else '', 'dockerfile':'Dockerfile','overlay_root':'deploy/overlays'}
        if profile=='service':
            name='task-worker' if kind=='worker' else 'user-api'
            values.update(service=name,kind=kind,dockerfile=name+'.Dockerfile',app_port='' if kind=='worker' else '1001',service_port='' if kind=='worker' else '1001',config_file='task.yaml' if kind=='worker' else name+'.yaml',overlay_root='deploy/worker_overlays' if kind=='worker' else 'deploy/overlays',config_name_variable=name.upper().replace('-','_')+'_CONFIG_SECRET_NAME',app_name_prefix_default='fixture',acr_repository_prefix_default='fixture')
        for key in W['on']['workflow_call']['inputs']: env['INPUT_'+key.upper()]=values.get(key,'')
        (root/'env').touch();(root/'output').touch()
        shutil.copytree(BASE/('render' if profile=='single-app' else 'services')/'deploy',root/'deploy')
        (root/values['dockerfile']).touch()
        return env

    def test_validation_render_rollout_and_equivalence(self):
        for profile in ['single-app','service']:
            for target in ['dev','sit','uat','production']:
                kinds=['rest_api','worker'] if profile=='service' else ['rest_api']
                for kind in kinds:
                    with self.subTest(profile=profile,target=target,kind=kind),tempfile.TemporaryDirectory() as d:
                        root=Path(d);env=self.fixture(root,profile,target,kind)
                        if profile=='single-app':env['DEPLOY_OVERLAY']='development' if target=='dev' else target
                        validation=execute(script('Validate workflow inputs'),env,root);self.assertEqual(validation.returncode,0,validation.stderr)
                        bad=dict(env,INPUT_ACR_REGISTRY='https://invalid');self.assertNotEqual(execute(script('Validate workflow inputs'),bad,root).returncode,0)
                        resolve='Resolve single application' if profile=='single-app' else 'Resolve service configuration'
                        result=execute(script(resolve),env,root);self.assertEqual(result.returncode,0,result.stderr)
                        apply_env(env,root/'env')
                        stub=root/'bin';stub.mkdir();kubectl=stub/'kubectl'
                        kubectl.write_text('#!/usr/bin/env python3\nimport os,sys,shutil\na=sys.argv[1:]\nwith open(os.environ["CALLS"],"a") as f:f.write(" ".join(a)+"\\n")\nif a[0]=="apply":shutil.copyfile(a[a.index("-f")+1],os.environ["MANIFEST"])\nif a[:2]==["rollout","status"] and os.environ.get("ROLLOUT_FAIL")=="1":sys.exit(1)\n');kubectl.chmod(0o755)
                        env.update(PATH=str(stub)+os.pathsep+os.environ['PATH'],CALLS=str(root/'calls'),MANIFEST=str(root/'manifest'))
                        result=execute(script('Render and deploy manifests'),env,root);self.assertEqual(result.returncode,0,result.stderr)
                        docs=list(yaml.safe_load_all((root/'manifest').read_text()))
                        if kind=='worker':self.assertFalse(any(doc['kind'] in ('Service','HorizontalPodAutoscaler') for doc in docs))
                        dep=next(doc for doc in docs if doc['kind']=='Deployment')
                        self.assertEqual(dep['spec']['template']['spec']['containers'][0]['image'],env['IMAGE_URL']+':'+env['IMAGE_TAG'])
                        self.assertNotRegex((root/'manifest').read_text(),r'__[A-Z0-9_]+__')
                        # Compare actual rendered manifests with execution of the original baseline path.
                        original=load(BASE/('render' if profile=='single-app' else 'services')/WF)
                        origstep=next(s for s in original['jobs']['build' if profile=='single-app' else 'build_and_deploy']['steps'] if s.get('name')==('Deploy to ACK' if profile=='single-app' else 'Render and deploy manifests'))['run']
                        if profile=='service':
                            (root/'scripts').mkdir();shutil.copy2(BASE/'services/scripts/render_deploy_placeholders.py',root/'scripts/render_deploy_placeholders.py')
                        else:
                            origstep=origstep.replace('${{ github.event_name }}','push').replace('${{ inputs.environment }}','')
                            # Original applies stdin; let the same stub capture it.
                            origstep=origstep.replace('kustomize build . | kubectl apply -f - -n "${K8S_NAMESPACE}"','kustomize build . > "${RUNTIME_DIR}/rendered.yaml"\nkubectl apply -f "${RUNTIME_DIR}/rendered.yaml" -n "${K8S_NAMESPACE}"')
                        env['MANIFEST']=str(root/'baseline-manifest')
                        original_result=execute(origstep,env,root);self.assertEqual(original_result.returncode,0,original_result.stderr)
                        self.assertEqual(docs,list(yaml.safe_load_all((root/'baseline-manifest').read_text())))
                        env['ROLLOUT_FAIL']='1';result=execute(script('Render and deploy manifests'),env,root)
                        self.assertNotEqual(result.returncode,0);self.assertIn('rollout undo',(root/'calls').read_text())
                        # Unknown placeholder fails before kubectl apply.
                        (root/'deploy/base/bad.yaml').write_text('unknown: __UNRESOLVED_FIXTURE__\n')
                        (root/'calls').write_text('');env['ROLLOUT_FAIL']='0'
                        self.assertNotEqual(execute(script('Render and deploy manifests'),env,root).returncode,0)
                        self.assertEqual((root/'calls').read_text(),'')

    def test_custom_overlay_staging_dependencies(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);env=self.fixture(root,'service')
            (root/'custom').mkdir();(root/'shared').mkdir()
            shutil.move(str(root/'deploy/overlays'),root/'custom/overlays')
            shutil.move(str(root/'deploy/base'),root/'shared/base')
            for path in (root/'custom/overlays').rglob('kustomization.yaml'):
                path.write_text(path.read_text().replace('../../base','../../../shared/base'))
            # A second local Kustomize dependency includes a generated file input.
            extra=root/'shared/settings';extra.mkdir()
            (extra/'kustomization.yaml').write_text('configMapGenerator:\n- name: fixture-settings\n  files:\n  - settings.txt\n')
            (extra/'settings.txt').write_text('synthetic=true\n')
            overlay=root/'custom/overlays/development/kustomization.yaml'
            data=yaml.safe_load(overlay.read_text());data['resources'].append('../../../shared/settings')
            overlay.write_text(yaml.safe_dump(data))
            shutil.rmtree(root/'deploy')
            env['INPUT_OVERLAY_ROOT']='custom/overlays'
            before={str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}
            for name in ['Validate workflow inputs','Resolve service configuration']:
                result=execute(script(name),env,root);self.assertEqual(result.returncode,0,result.stderr)
            apply_env(env,root/'env')
            stub=root/'bin';stub.mkdir();kubectl=stub/'kubectl'
            kubectl.write_text('#!/usr/bin/env python3\nimport os,sys,shutil\na=sys.argv[1:]\nif a[0]=="apply":shutil.copyfile(a[a.index("-f")+1],os.environ["MANIFEST"])\n');kubectl.chmod(0o755)
            env.update(PATH=str(stub)+os.pathsep+os.environ['PATH'],MANIFEST=str(root/'manifest'))
            result=execute(script('Render and deploy manifests'),env,root)
            self.assertEqual(result.returncode,0,result.stderr)
            docs=list(yaml.safe_load_all((root/'manifest').read_text()))
            self.assertTrue(any(doc['kind']=='ConfigMap' and doc['data']=={'settings.txt':'synthetic=true\n'} for doc in docs))
            dep=next(doc for doc in docs if doc['kind']=='Deployment')
            self.assertEqual(dep['spec']['template']['spec']['containers'][0]['image'],env['IMAGE_URL']+':'+env['IMAGE_TAG'])
            self.assertNotRegex((root/'manifest').read_text(),r'__[A-Z0-9_]+__')
            for name,content in before.items():
                if name not in ('env','output'):self.assertEqual((root/name).read_bytes(),content,name)
            self.assertFalse((root/'deploy').exists())

    def test_initializer(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);subprocess.run(['git','init','-q',str(root)],check=True);(root/'Dockerfile').write_text('FROM scratch\n')
            args=['bash',str(CENTRAL/'init.sh'),'--strategy','ack','--app','fixture-product-app','--customer','fixture','--product','product','--module','app','--port','8000','--health','/health','--environment-map',json.dumps({'development':{'overlay':'development','environment':'dev'}}),'--central-sha','a'*40,'--yes']
            before=subprocess.run(['git','status','--porcelain'],cwd=root,text=True,capture_output=True).stdout
            result=subprocess.run(args+['--dry-run'],cwd=root,text=True,capture_output=True);self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(before,subprocess.run(['git','status','--porcelain'],cwd=root,text=True,capture_output=True).stdout)
            self.assertFalse((root/'.git/index').exists())
            result=subprocess.run(args,cwd=root,text=True,capture_output=True);self.assertEqual(result.returncode,0,result.stderr)
            self.assertFalse((root/'.git/index').exists())
            self.assertEqual(list((root/'.git/refs/heads').iterdir()),[])
            caller=load(root/'.github/workflows/deploy.yaml');self.assertNotIn('push',caller['on'])
            dispatch=yaml.safe_load((root/'.github/workflows/deploy.yaml').read_text())['on']['workflow_dispatch']['inputs']
            expected_required={'instance_code':True,'project_name':True,'business_module':True,'environment':True,'description':False,'approval_time':False,'release_type':True}
            self.assertEqual(set(dispatch),set(expected_required))
            for name,required in expected_required.items():
                self.assertIs(dispatch[name]['required'],required,name)
            self.assertTrue(caller['jobs']['build']['uses'].endswith('@'+'a'*40))
            self.assertEqual(caller['jobs']['build']['if'], "${{ contains(fromJSON('[\"development\"]'), github.ref_name) }}")
            self.assertFalse((root/'deploy/base/hpa.yaml').exists())
            result=subprocess.run(args,cwd=root,text=True,capture_output=True);self.assertNotEqual(result.returncode,0)

if __name__=='__main__':unittest.main(verbosity=2)
