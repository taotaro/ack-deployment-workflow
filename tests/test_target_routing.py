#!/usr/bin/env python3
"""Execute the actual inline target resolver with synthetic Environment contexts only."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import yaml
import test_workflow as regression
from test_workflow import CENTRAL, IMPL, WF, W, STEPS, execute, script, apply_env, load

SUFFIXES = ('REGION_ID', 'ACR_REGISTRY', 'ACR_INSTANCE_ID', 'ACK_CLUSTER_ID',
            'ALICLOUD_ACCESS_KEY_ID', 'ALICLOUD_ACCESS_KEY_SECRET')
SECRET_NAMES = {f'TAOTARO_{p}_{s}' for p in ('PROD', 'NONPROD', 'TARGET') for s in SUFFIXES[4:]}
LEGACY_SECRET_NAMES = {s.replace('TAOTARO_', 'LUMIO_', 1) for s in SECRET_NAMES}

def bundle(prefix, label, namespace='TAOTARO'):
    values = (label+'-region', label+'.example:443', label+'-instance', label+'-cluster', label+'-ak', label+'-sk')
    return {f'{namespace}_{prefix}_{s}': v for s, v in zip(SUFFIXES, values)}

class Routing(unittest.TestCase):
    def fixture(self, root, profile='single-app', identity='dev'):
        env = regression.Proof().fixture(root, profile, identity if profile == 'service' else 'dev')
        env.update(INPUT_TARGET_ROUTING='environment', ENV_NAME=identity)
        for suffix in SUFFIXES[:4]: env['INPUT_'+suffix] = ''
        for suffix in SUFFIXES[4:]: env[suffix] = ''
        for name in list(env):
            if name.startswith(('TAOTARO_', 'LUMIO_')): env.pop(name)
        env.update({**bundle('PROD', 'prod'), **bundle('NONPROD', 'test')})
        return env

    def resolve(self, env, root, success=True):
        (root/'env').write_text('')
        result = execute(script('Resolve infrastructure target'), env, root)
        self.assertEqual(result.returncode == 0, success, result.stderr)
        # Neither selected nor unused credentials appear in process logs.
        for namespace in ('TAOTARO', 'LUMIO'):
            for prefix in ('PROD', 'NONPROD', 'TARGET'):
                for suffix in SUFFIXES[4:]:
                    value = env.get(f'{namespace}_{prefix}_{suffix}')
                    if value: self.assertNotIn(value, result.stdout+result.stderr)
        if success:
            resolved = dict(env); apply_env(resolved, root/'env')
            return resolved
        self.assertEqual((root/'env').read_text(), '')

    def test_aliases_and_unselected_empty(self):
        for identity in ('prod', 'prd', 'production', 'dev', 'development', 'sit', 'uat', 'preview'):
            with self.subTest(identity=identity), tempfile.TemporaryDirectory() as d:
                root=Path(d); env=self.fixture(root, identity=identity)
                label='prod' if identity in ('prod','prd','production') else 'test'
                unused='NONPROD' if label=='prod' else 'PROD'
                for s in SUFFIXES: env[f'TAOTARO_{unused}_{s}']=''
                resolved=self.resolve(env,root)
                self.assertEqual([resolved['RESOLVED_'+s] for s in SUFFIXES], list(bundle('TARGET',label).values()))
                self.assertEqual(resolved['IMAGE_URL'], label+'.example:443/team/app')
        for identity in ('dev','sit','uat','production'):
            with self.subTest(service=identity), tempfile.TemporaryDirectory() as d:
                root=Path(d); env=self.fixture(root,'service',identity)
                result=execute(script('Validate workflow inputs'),env,root)
                self.assertEqual(result.returncode,0,result.stderr)
                resolved=self.resolve(env,root)
                self.assertEqual(resolved['RESOLVED_ACK_CLUSTER_ID'],('prod' if identity=='production' else 'test')+'-cluster')
                # Service image construction must use the same selected registry.
                result=execute(script('Resolve service configuration'),resolved,root)
                self.assertEqual(result.returncode,0,result.stderr);apply_env(resolved,root/'env')
                self.assertTrue(resolved['IMAGE_URL'].startswith(resolved['RESOLVED_ACR_REGISTRY']+'/'))

    def test_full_override_and_same_named_application_environments(self):
        for repository in ('application-a','application-b'):
            with tempfile.TemporaryDirectory() as d:
                root=Path(d);env=self.fixture(root,identity='prod')
                env.update(REPOSITORY=repository,INPUT_GITHUB_ENVIRONMENT='development-env', **bundle('TARGET',repository))
                # Override is sufficient with BOTH default bundles absent.
                for p in ('PROD','NONPROD'):
                    for s in SUFFIXES:env[f'TAOTARO_{p}_{s}']=''
                resolved=self.resolve(env,root)
                self.assertEqual([resolved['RESOLVED_'+s] for s in SUFFIXES],list(bundle('TARGET',repository).values()))
        for service in ('user-api','business-api','payment-api','task-worker'):
            with tempfile.TemporaryDirectory() as d:
                root=Path(d);env=self.fixture(root,'service','production');env['INPUT_SERVICE']=service
                resolved=self.resolve(env,root)
                self.assertEqual(resolved['RESOLVED_ACK_CLUSTER_ID'],'prod-cluster')

    def test_incomplete_invalid_missing_and_unknown_fail_closed(self):
        for missing in SUFFIXES:
            with self.subTest(missing=missing),tempfile.TemporaryDirectory() as d:
                root=Path(d);env=self.fixture(root);env.update(bundle('TARGET','alternate'))
                env['TAOTARO_TARGET_'+missing]='';self.resolve(env,root,False)
            with tempfile.TemporaryDirectory() as d:
                root=Path(d);env=self.fixture(root);env['TAOTARO_NONPROD_'+missing]='';self.resolve(env,root,False)
        for only in SUFFIXES:
            with tempfile.TemporaryDirectory() as d:
                root=Path(d);env=self.fixture(root);env['TAOTARO_TARGET_'+only]=bundle('TARGET','alternate')['TAOTARO_TARGET_'+only]
                self.resolve(env,root,False)
        cases=[('ENV_NAME','unknown'),('INPUT_TARGET_ROUTING','anything'),('INPUT_REGION_ID','legacy-region'),
               ('ALICLOUD_ACCESS_KEY_ID','legacy-ak'),('INPUT_TARGET_ENV','production'),
               ('TAOTARO_NONPROD_ACR_REGISTRY','https://bad'),('TAOTARO_NONPROD_ACR_REGISTRY','test.example:70000'),
               ('TAOTARO_NONPROD_REGION_ID','region\ninjection'),('TAOTARO_NONPROD_ACR_INSTANCE_ID','instance space'),
               ('TAOTARO_NONPROD_ACK_CLUSTER_ID','../cluster'),('TAOTARO_NONPROD_ALICLOUD_ACCESS_KEY_SECRET','secret space')]
        for name,value in cases:
            with self.subTest(name=name,value=value),tempfile.TemporaryDirectory() as d:
                root=Path(d);env=self.fixture(root);env[name]=value;self.resolve(env,root,False)
        for identity in ('unknown','prod','development'):
            with tempfile.TemporaryDirectory() as d:
                root=Path(d);env=self.fixture(root,'service','dev');env['INPUT_TARGET_ENV']=identity
                self.resolve(env,root,False)
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);env=self.fixture(root,'service','dev');env['INPUT_GITHUB_ENVIRONMENT']='production-env'
            self.assertNotEqual(execute(script('Validate workflow inputs'),env,root).returncode,0)

    def test_fallback_custom_overlay_dispatch_and_explicit_compatibility(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);env=self.fixture(root);env.update(ENV_NAME='',DEPLOY_OVERLAY='preview')
            self.assertEqual(self.resolve(env,root)['RESOLVED_ACK_CLUSTER_ID'],'test-cluster')
            env.update(ENV_NAME='prod',DEPLOY_OVERLAY='custom-overlay')
            resolved=self.resolve(env,root)
            self.assertEqual(resolved['RESOLVED_ACK_CLUSTER_ID'],'prod-cluster')
            (root/'deploy/overlays/custom-overlay').mkdir()
            self.assertEqual(execute(script('Validate workflow inputs'),env,root).returncode,0)
            resolved.update(EVENT_NAME='workflow_dispatch',RELEASE_ENVIRONMENT='dev')
            self.assertNotEqual(execute(script('Resolve single application'),resolved,root).returncode,0)
            resolved['RELEASE_ENVIRONMENT']='prd'
            self.assertEqual(execute(script('Resolve single application'),resolved,root).returncode,0)
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);env=regression.Proof().fixture(root,'single-app');env['INPUT_TARGET_ROUTING']='explicit'
            env.update(bundle('TARGET','unused'),ENV_NAME='legacy-custom')
            resolved=self.resolve(env,root)
            self.assertEqual(resolved['RESOLVED_ACK_CLUSTER_ID'],'synthetic-cluster')
            env['ALICLOUD_ACCESS_KEY_SECRET']='';self.resolve(env,root,False)

    def test_legacy_callers_survive_staged_default_variables(self):
        for profile, identity, selected in [('single-app', 'dev', 'NONPROD'), ('service', 'production', 'PROD')]:
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as d:
                root=Path(d);env=self.fixture(root,profile,identity)
                env.update(bundle(selected,'legacy','LUMIO'))
                for s in SUFFIXES[4:]:env[f'TAOTARO_{selected}_{s}']=''
                # New default Variables, even partial, are merely staged while
                # this caller still supplies the complete legacy credentials.
                env[f'TAOTARO_{selected}_REGION_ID']=''
                resolved=self.resolve(env,root)
                self.assertEqual([resolved['RESOLVED_'+s] for s in SUFFIXES],list(bundle(selected,'legacy','LUMIO').values()))
                env.update(bundle('TARGET','legacy-target','LUMIO'))
                resolved=self.resolve(env,root)
                self.assertEqual(resolved['RESOLVED_ACK_CLUSTER_ID'],'legacy-target-cluster')

    def test_canonical_namespace_precedence_and_no_field_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);env=self.fixture(root);env.update(bundle('NONPROD','legacy','LUMIO'))
            resolved=self.resolve(env,root)
            self.assertEqual([resolved['RESOLVED_'+s] for s in SUFFIXES],list(bundle('NONPROD','test').values()))
        for missing in SUFFIXES:
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as d:
                root=Path(d);env=self.fixture(root);env.update(bundle('NONPROD','legacy','LUMIO'))
                env['TAOTARO_NONPROD_'+missing]=''
                self.resolve(env,root,False)

    def test_cross_namespace_target_never_ignored_or_mixed(self):
        for only in SUFFIXES:
            with self.subTest(only=only), tempfile.TemporaryDirectory() as d:
                root=Path(d);env=self.fixture(root);env.update(bundle('NONPROD','legacy','LUMIO'))
                for s in SUFFIXES[4:]:env['TAOTARO_NONPROD_'+s]=''
                env['TAOTARO_TARGET_'+only]=bundle('TARGET','alternate')['TAOTARO_TARGET_'+only]
                self.resolve(env,root,False)
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);env=self.fixture(root);env.update(bundle('TARGET','legacy-target','LUMIO'))
            self.resolve(env,root,False)  # Never discard the old override for a new default.
            env.update(bundle('TARGET','canonical-target'))
            resolved=self.resolve(env,root)
            self.assertEqual([resolved['RESOLVED_'+s] for s in SUFFIXES],list(bundle('TARGET','canonical-target').values()))

    def test_thin_callers_schema_and_consistent_cloud_bindings(self):
        schema=W['on']['workflow_call']
        self.assertEqual(schema['inputs']['target_routing']['default'],'explicit')
        for s in SECRET_NAMES | LEGACY_SECRET_NAMES:self.assertEqual(str(schema['secrets'][s]['required']).lower(),'false')
        for namespace in ('TAOTARO', 'LUMIO'):
            for prefix in ('PROD', 'NONPROD', 'TARGET'):
                for s in SUFFIXES:
                    name=f'{namespace}_{prefix}_{s}'
                    context='vars' if s in SUFFIXES[:4] else 'secrets'
                    self.assertEqual(W['jobs']['deploy']['env'][name], '${{ '+context+'.'+name+' }}')
        for path in (CENTRAL/'templates/ack/caller.yaml.tmpl', IMPL/'render'/WF, IMPL/'services'/WF):
            caller=load(path);job=caller['jobs'].get('build',caller['jobs'].get('build_and_deploy'))
            self.assertEqual(job['with']['target_routing'],'environment')
            self.assertFalse(set(job['with']) & {'region_id','acr_registry','acr_instance_id','ack_cluster_id'})
            self.assertFalse(set(job['secrets']) & set(SUFFIXES[4:]))
            for s in SECRET_NAMES:self.assertEqual(job['secrets'][s],'${{ secrets.'+s+' }}')
            self.assertNotIn('environment',job)
        names=[s.get('name') for s in STEPS]
        resolver=names.index('Resolve infrastructure target')
        cloud=[s for s in STEPS if s.get('uses','').startswith(('aliyun/', 'alibaba-cloud/'))]
        self.assertEqual(len(cloud),2)
        for action in cloud:
            self.assertGreater(STEPS.index(action),resolver)
            self.assertEqual(action['with']['access-key-id'],'${{ env.RESOLVED_ALICLOUD_ACCESS_KEY_ID }}')
            self.assertEqual(action['with']['access-key-secret'],'${{ env.RESOLVED_ALICLOUD_ACCESS_KEY_SECRET }}')
        self.assertNotIn('GITHUB_OUTPUT',script('Resolve infrastructure target'))

    def test_initializer_unknown_environment_refused(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);subprocess.run(['git','init','-q',str(root)],check=True);(root/'Dockerfile').write_text('FROM scratch\n')
            args=['python3',str(CENTRAL/'init-ack.py'),'--app','fixture-product-app','--customer','fixture','--product','product','--module','app','--port','8000','--health','/health','--environment-map',json.dumps({'main':{'overlay':'custom','environment':'unknown'}}),'--central-sha','a'*40,'--yes']
            result=subprocess.run(args,cwd=root,text=True,capture_output=True)
            self.assertNotEqual(result.returncode,0);self.assertIn('Unknown routing environment',result.stderr)
            self.assertFalse((root/'.github').exists())

if __name__=='__main__':unittest.main(verbosity=2)
