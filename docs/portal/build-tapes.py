#!/usr/bin/env python3
"""Build course tapes for the Agent School portal from raw cluster snapshots.

Usage: build-tapes.py <course> <cluster> <raw-tape.json[.gz]> <manual.md> <out.json>

The generator slices the raw snapshot (shared/tapes/*-tape-raw.json.gz) into the
per-course tape format described in shared/tapes/TAPE-SCHEMA.md. Steps mirror the
course MANUAL.md exactly (parsed from it). Terminal playback uses captured pod
logs where the pod survived to capture time; short excerpts reconstructed from
session transcripts are marked "reconstructed".

NOTE (QA 2026-07-28): build_101 still emits the pre-cmds step format and does not
add kimi-isvc; the shipped tapes/101-venice.json was hand-finished after generation.
Do NOT regenerate 101 until build_101 is ported to the cmds format (201-302 are
faithful: they regenerate byte-identical, modulo intended changes).
"""
import json, gzip, re, sys

def load_raw(path):
    op = gzip.open if path.endswith('.gz') else open
    with op(path, 'rt') as f:
        return json.load(f)

def parse_manual(path):
    """Extract [{n, title, why, do, expect}] from a MANUAL.md."""
    text = open(path).read()
    steps = []
    for m in re.finditer(r'^## Step (\d+): (.+?)$(.*?)(?=^## )', text, re.M | re.S):
        n, title, body = int(m.group(1)), m.group(2).strip(), m.group(3)
        def grab(tag):
            mm = re.search(r'\*\*' + tag + r'[^*]*\*\*:?\s*(.*?)(?=\n\*\*|\n## |\Z)', body, re.S)
            return mm.group(1).strip() if mm else ''
        steps.append({'n': n, 'title': title, 'why': grab('Why'),
                      'do': grab(r'Do(?: \(Console\))?'), 'expect': grab('Expect')})
    return steps

def log_to_lines(text, head=0, tail=60, speed_cap_ms=350):
    """Convert a timestamped log into [[t_ms, line], ...] with capped gaps."""
    lines = [l for l in text.split('\n') if l.strip()]
    if head and tail and len(lines) > head + tail:
        lines = lines[:head] + ['... [%d lines elided] ...' % (len(lines) - head - tail)] + lines[-tail:]
    elif tail and len(lines) > tail:
        lines = lines[-tail:]
    out, t = [], 0
    for l in lines:
        l2 = re.sub(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z\s?', '', l)
        l2 = re.sub(r'\x1b\[[0-9;]*m', '', l2)
        out.append([t, l2[:400]])
        t += min(speed_cap_ms, 120 + len(l2) // 3)
    return out

def slim(obj, keep_status=True):
    o = {'apiVersion': obj.get('apiVersion'), 'kind': obj.get('kind'),
         'metadata': {k: obj['metadata'].get(k) for k in ('name', 'namespace', 'labels', 'creationTimestamp')},
         'spec': obj.get('spec')}
    if keep_status:
        o['status'] = obj.get('status')
    return o

KIND_MAP = {
    'configmaps': 'ConfigMap', 'pods': 'Pod', 'pvcs': 'PersistentVolumeClaim',
    'persistentvolumeclaims': 'PersistentVolumeClaim', 'serviceaccounts': 'ServiceAccount',
    'cronjobs': 'CronJob', 'deployments': 'Deployment', 'services': 'Service',
    'secrets': 'Secret', 'jobs': 'Job', 'rayjobs': 'RayJob', 'localqueues': 'LocalQueue',
    'featurestores': 'FeatureStore', 'inferenceservices': 'InferenceService',
    'workloads': 'Workload', 'authpolicies': 'AuthPolicy', 'httproutes': 'HTTPRoute',
    'ratelimitpolicies': 'RateLimitPolicy',
}

def find(raw, ns, kind, name_prefix):
    for it in raw['resources'].get(ns, {}).get(kind, []):
        if it['metadata']['name'].startswith(name_prefix):
            if not it.get('kind') and kind in KIND_MAP:
                it['kind'] = KIND_MAP[kind]  # raw k8s list items omit kind; QA F13
            return it
    return None

# ---------------------------------------------------------------- course configs
def build_101(raw, manual_steps):
    ns = 'agent-school'
    A = {'resources': {}, 'logs': {}, 'mlflow': {}, 'registry': [], 'jobsTimeline': [
        {'name':'feast-bootstrap','status':'Succeeded','after':5},{'name':'feast-save-datasets','status':'Succeeded','after':5},
        {'name':'noc-ask-jb2qw','status':'Succeeded','after':6},{'name':'anomaly-contamination-sweep','status':'Succeeded (RayJob)','after':7}]}
    def put(kind, name_prefix, key=None, keep_status=True):
        it = find(raw, ns, kind, name_prefix)
        if it:
            A['resources'][key or f"{ns}/{kind}/{it['metadata']['name']}"] = slim(it, keep_status)
            return it
        return None

    fs   = put('featurestores', 'fivegprod', 'featurestore')
    feast_pod = find(raw, ns, 'pods', 'feast-fivegprod')
    if feast_pod: A['resources']['feast-pod'] = slim(feast_pod)
    put('pvcs', 'feast-fivegprod-online', 'pvc-online'); put('pvcs', 'feast-fivegprod-registry', 'pvc-registry')
    put('localqueues', 'agent-school-queue', 'localqueue')
    put('cronjobs', 'noc-sweep', 'cronjob')
    put('serviceaccounts', 'noc-assistant', 'sa')
    put('configmaps', 'mlflow-tracking', 'cm-mlflow')
    put('configmaps', 'feature-store-client', 'cm-feast-client')
    rj = put('rayjobs', 'anomaly-contamination-sweep', 'rayjob')
    for w in raw['resources'][ns].get('workloads', []):
        if 'anomaly' in w['metadata']['name'] or 'rayjob' in w['metadata']['name']:
            A['resources']['kueue-workload'] = slim(w)

    # logs (real captures)
    L = raw['logs']
    def log(key_sub, out_key, **kw):
        for k, v in L.items():
            if key_sub in k:
                A['logs'][out_key] = log_to_lines(v, **kw); return True
        return False
    log('noc-assistant-1-build', 'build', head=12, tail=45)
    log('feast-fivegprod', 'feast-online', tail=25)
    log('ray-job-submitter', 'ray-submit', head=10, tail=55)
    log('/ray-head', 'ray-head', tail=25)

    # mlflow + registry
    ml = raw['rest'].get('mlflow', {})
    A['mlflow']['experiments'] = [e for e in ml.get('experiments', [])
                                  if e['meta']['name'] in ('101-noc-assistant', '5gprod-anomaly-sweep')]
    A['registry'] = [x for x in raw['rest'].get('model_registry', [])
                     if x['m']['name'] == '5gprod-anomaly-isolationforest']

    # Reconstructed transcripts (pods TTL-expired before capture; text from the
    # live session records of the July 28 Venice run).
    R = {
      'ns': [[0,'$ oc new-project agent-school'],[400,'Now using project "agent-school" on server "https://api.venice.narlabs.io:6443".'],[700,'$ oc label ns agent-school kueue.openshift.io/managed=true opendatahub.io/dashboard=true opendatahub.io/feast=true'],[1100,'namespace/agent-school labeled']],
      'wiring': [[0,'secret/llm-credentials created'],[280,'configmap/mlflow-tracking created'],[520,'configmap/feature-store-client created']],
      'rbac': [[0,'rolebinding.rbac.authorization.k8s.io/noc-assistant-mlflow created'],[240,'rolebinding.rbac.authorization.k8s.io/agent-school-serviceaccounts-mlflow created'],[480,'localqueue.kueue.x-k8s.io/agent-school-queue created'],[720,'featurestore.feast.dev/fivegprod created'],[1500,'... feast operator reconciling ...'],[2600,'pod/feast-fivegprod-665dcd564c-wxq9d   1/1   Running']],
      'feast': [[0,'job.batch/feast-bootstrap created'],[900,'[pip] installing feast pandas pyarrow scikit-learn ...'],[2600,'[ingest] amf: 1441 rows -> data/amf_features.parquet'],[2900,'[ingest] smf: 1441 rows -> data/smf_features.parquet'],[3200,'[ingest] upf: 1441 rows -> data/upf_features.parquet'],[3600,'feast apply: registered entity nf, 3 feature views, 1 feature service'],[4200,'[push] amf: latest vector (2025-01-17 12:01:55) -> online store'],[4450,'[push] smf: latest vector (2025-01-17 12:01:55) -> online store'],[4700,'[push] upf: latest vector (2025-01-17 12:01:55) -> online store'],[5200,'job feast-bootstrap: Succeeded'],[5600,'job.batch/feast-save-datasets created ... Succeeded (3 SavedDatasets)']],
      'ask': [[0,'job.batch/noc-ask-jb2qw created'],[1200,'[agent] reading feature store: get_kpi_summary, detect_anomalies, check_alerts'],[2400,'[agent] LLM reasoning via kimi-linear-48b-a3b ...'],[3600,'ANSWER:'],[3900,'The 5G core is experiencing an AMF registration storm with cascading effects.'],[4200,'1. Immediate (0-15 min): rate-limit initial registrations at the AMF;'],[4500,'   verify N4 interface connectivity between SMF and UPF.'],[4800,'2. Medium-term (15-60 min): review registration patterns to identify the'],[5100,'   source; implement proper load balancing for AMF instances.'],[5400,'The network is degraded but not in complete failure. Immediate action on'],[5650,'the AMF registration storm should resolve the cascading alerts.'],[6100,'[mlflow] trace flushed to experiment 101-noc-assistant']],
    }

    S = manual_steps
    def step(i, action, playback, check=None, wrong=None):
        s = {'id': f'101-{i}', 'n': i, 'title': S[i-1]['title'], 'why': S[i-1]['why'],
             'do': S[i-1]['do'], 'expect': S[i-1]['expect'], 'action': action, 'playback': playback}
        if check: s['check'] = check
        if wrong: s['wrongTurns'] = wrong
        return s

    steps = [
      step(1, {'kind':'terminal','label':'Create the project'},
              {'terminal': R['ns'], 'reconstructed': True,
               'dashboard': {'panel':'resources','note':'Project agent-school appears with its three labels.'}}),
      step(2, {'kind':'import-yaml','label':'Import YAML: secret + 2 ConfigMaps',
               'yamlAsset':'cm-mlflow'},
              {'terminal': R['wiring'], 'reconstructed': True,
               'reveal':['agent-school/configmaps/mlflow-tracking','cm-mlflow','cm-feast-client'],
               'dashboard': {'panel':'resources','note':'llm-credentials, mlflow-tracking, feature-store-client exist. Secret values are never shown; this is the wiring, not the data.'}},
              check={'asset':'cm-mlflow','path':'metadata.name','equals':'mlflow-tracking'}),
      step(3, {'kind':'start-build','label':'Apply base + Start build noc-assistant'},
              {'terminal':'build', 'reveal':['sa','cronjob'],
               'dashboard': {'panel':'resources','note':'ServiceAccount noc-assistant (the agent identity) and the suspended noc-sweep CronJob. The build log on the left is the real recorded build.'}},
              check={'asset':'sa','path':'metadata.name','equals':'noc-assistant'}),
      step(4, {'kind':'import-yaml','label':'Import YAML: RBAC + LocalQueue + FeatureStore'},
              {'terminal': R['rbac'], 'reconstructed': True,
               'reveal':['localqueue','featurestore','feast-pod','pvc-online','pvc-registry'],
               'dashboard': {'panel':'featurestore','note':'The feast operator stood up the store: pod Running, two PVCs Bound.'}},
              check={'asset':'feast-pod','path':'status.phase','equals':'Running'}),
      step(5, {'kind':'create-job','label':'Create feast-bootstrap + feast-save-datasets Jobs'},
              {'terminal': R['feast'], 'reconstructed': True,
               'dashboard': {'panel':'featurestore','note':'Offline parquet engineered, features applied, latest vectors pushed online. The agent can now read live network state.'}}),
      step(6, {'kind':'create-job','label':'Create the noc-ask Job (one agent run = one Job)'},
              {'terminal': R['ask'], 'reconstructed': True,
               'dashboard': {'panel':'experiments','note':'The run lands in Experiments with full traces: identity passed auth, config enabled tracking.'}}),
      step(7, {'kind':'create-job','label':'Create ray-sweep-src ConfigMap + RayJob'},
              {'terminal':'ray-submit', 'reveal':['rayjob','kueue-workload'],
               'dashboard': {'panel':'workloads','note':'Kueue admitted the RayJob against the shared ClusterQueue; the sweep result is in Experiments (5gprod-anomaly-sweep) and the tuned detector in the model registry.'}},
              check={'asset':'rayjob','path':'status.jobStatus','equals':'SUCCEEDED'}),
    ]
    return A, steps


# ---------------------------------------------------------------- shared
def slim_dep(it):
    o = slim(it)
    o['status'] = {k: (it.get('status') or {}).get(k) for k in ('availableReplicas','readyReplicas')}
    return o

def isvc_slim(it):
    return {'kind':'InferenceService','metadata':{'name':it['metadata']['name'],'namespace':it['metadata']['namespace']},
            'spec':{'predictor':{'model':{'runtime':it['spec']['predictor']['model'].get('runtime')}}},
            'status':{'ready': any(c.get('type')=='Ready' and c.get('status')=='True' for c in (it.get('status') or {}).get('conditions',[]))}}

def trim_runs(exp, n_runs=10, n_kv=6):
    exp = dict(exp)
    runs = []
    for r in exp.get('runs', [])[:n_runs]:
        r = dict(r)
        d = dict(r.get('data') or {})
        d['metrics'] = (d.get('metrics') or [])[:n_kv]
        d['params'] = (d.get('params') or [])[:n_kv]
        r['data'] = d
        r.pop('inputs', None)
        runs.append(r)
    exp['runs'] = runs
    return exp

def mk_step(course, S, i, cmds, panel, note, recon, reveal=None, check=None):
    s = {'id': f'{course}-{i}', 'n': i, 'title': S[i-1]['title'], 'why': S[i-1]['why'],
         'do': S[i-1]['do'], 'expect': S[i-1]['expect'], 'cmds': cmds,
         'action': {'label': S[i-1]['title']},
         'playback': {'dashboard': {'panel': panel, 'note': note}, 'reconstructed': recon}}
    if reveal: s['playback']['reveal'] = reveal
    if check: s['check'] = check
    return s

def C(cmd, *out_lines, log=None):
    if log: return {'cmd': cmd, 'log': log}
    return {'cmd': cmd, 'out': [[i*260, l] for i, l in enumerate(out_lines)]}


def add_kimi(raw, A):
    """Course LLM: the Kimi InferenceService every course calls (telco-aix ns)."""
    it = find(raw, 'telco-aix', 'inferenceservices', 'kimi-linear')
    if it: A['resources']['kimi-isvc'] = isvc_slim(it)

# ---------------------------------------------------------------- 201
def build_201(raw, S):
    ns = 'agent-school'
    A = {'resources': {}, 'logs': {}, 'mlflow': {}, 'registry': [], 'jobsTimeline': []}
    add_kimi(raw, A)
    dep = find(raw, ns, 'deployments', 'rca-rag')
    if dep: A['resources']['rca-rag'] = slim_dep(dep)
    sa = find(raw, ns, 'serviceaccounts', 'rca-investigator')
    if sa: A['resources']['sa-rca'] = slim(sa, False)
    L = raw['logs']
    for k, v in L.items():
        if 'rca-investigator-1-build' in k: A['logs']['build'] = log_to_lines(v, head=10, tail=35)
        if 'rca-rag' in k and '/rag' in k: A['logs']['rag'] = log_to_lines(v, tail=16)
    ml = raw['rest'].get('mlflow', {})
    A['mlflow']['experiments'] = [trim_runs(e) for e in ml.get('experiments', []) if e['meta']['name'] == '201-rca-investigator']
    A['jobsTimeline'] = [{'name':'rca-run-p56cq','status':'Succeeded','after':2},{'name':'rca-judge','status':'Succeeded','after':3}]
    D = '201-rca-investigator/deploy/ocp'
    steps = [
      mk_step('201', S, 1, [
        C(f'oc apply -f {D}/base/serviceaccount.yaml', 'serviceaccount/rca-investigator created'),
        C(f'oc apply -f {D}/base/imagestream-buildconfig.yaml', 'imagestream.image.openshift.io/rca-investigator created', 'buildconfig.build.openshift.io/rca-investigator created'),
        C(f'oc apply -f {D}/base/rag-service.yaml', 'deployment.apps/rca-rag created', 'service/rca-rag created'),
        C(f'oc apply -f {D}/rome/mlflow-rbac.yaml', 'rolebinding.rbac.authorization.k8s.io/rca-investigator-mlflow created'),
        C('oc start-build rca-investigator --follow', log='build'),
        C('oc get pods -n agent-school -l app.kubernetes.io/name=rca-rag',
          'NAME                       READY   STATUS    AGE', 'rca-rag-764bc9bb8f-dk4nn   1/1     Running   64s')],
        'resources', 'The rca-rag Deployment is the pattern-2 skill backend: the retrieval index lives behind a Service, never in the agent pod.', False,
        reveal=['rca-rag','sa-rca'], check={'asset':'rca-rag','path':'status.availableReplicas','equals':1}),
      mk_step('201', S, 2, [
        C(f'oc create -f {D}/job-rca.yaml', 'job.batch/rca-run-p56cq created'),
        C('oc logs -f job/rca-run-p56cq -n agent-school',
          '[agent] phase 1: investigation (max 4 turns) via http://rca-rag:8201',
          '[tool] search_evidence("registration failures amf") -> 6 hits',
          '[tool] get_metric_window("amf", "registration_success_rate") -> degraded 11:44-12:01',
          '[agent] phase 2: report write-up',
          'RCA: AMF registration storm triggered by resource exhaustion [amf-796], [alert-5]',
          'Impact: cascading session failures on SMF/UPF [smf-armed], [alert-7]',
          'Recommendations:',
          '  1. Rate-limit initial registrations at the AMF [alert-5]',
          '  2. Verify N4 interface connectivity between SMF and UPF [amf-796]',
          '  3. Implement early-warning thresholds for CPU/memory [alert-5], [amf-796]',
          '[mlflow] traces flushed (investigation + write-up)')],
        'experiments', 'Both phases land as traces: the tool-calling investigation and the report write-up, every claim citing evidence ids.', True),
      mk_step('201', S, 3, [
        C("oc create configmap rca-eval-src -n agent-school \\\n    --from-file=judge_evidence_grounding.py=201-rca-investigator/eval/judge_evidence_grounding.py", 'configmap/rca-eval-src created'),
        C(f'oc apply -f {D}/rome/job-judge.yaml', 'job.batch/rca-judge created'),
        C('oc logs -f job/rca-judge -n agent-school',
          '[judge] pulling latest 3 traces from workspace agent-school',
          '[judge] model: kimi-linear-48b-a3b via hosted_vllm (in-cluster)',
          '[judge] trace 1: claims grounded in cited evidence - PASS',
          '[judge] trace 2: grounded - PASS',
          '[judge] trace 3: describes tool calls without grounded claims - FLAG',
          'logged feedback for 3 traces')],
        'experiments', 'The judge wrote its verdicts back as feedback attached to the traces: quality became queryable platform data.', True)]
    return A, steps

# ---------------------------------------------------------------- 202
def build_202(raw, S):
    ns = 'agent-school'
    A = {'resources': {}, 'logs': {}, 'mlflow': {}, 'registry': [], 'jobsTimeline': [], 'pipelineRuns': []}
    add_kimi(raw, A)
    dspa = find(raw, ns, 'dspas', 'dspa')
    if dspa:
        A['resources']['dspa'] = {'kind':'DataSciencePipelinesApplication','metadata':{'name':'dspa','namespace':ns},
            'status':{'conditions':[{'type':c['type'],'status':c['status']} for c in (dspa.get('status') or {}).get('conditions',[])]}}
    isvc = find(raw, ns, 'inferenceservices', 'fraud-detector')
    if isvc: A['resources']['fraud-isvc'] = isvc_slim(isvc)
    wf = (raw['resources'][ns].get('workflows') or [None])[0]
    if wf:
        nodes = [{'name': n.get('displayName',''), 'type': n.get('type'), 'phase': n.get('phase')}
                 for n in (wf.get('status') or {}).get('nodes', {}).values() if n.get('type') in ('Pod','DAG')]
        A['pipelineRuns'] = [{'name':'fraud-brf-training','run':'fraud-brf-run-1','phase':(wf.get('status') or {}).get('phase','Succeeded'),
                             'progress':(wf.get('status') or {}).get('progress',''),'nodes':nodes}]
    L = raw['logs']
    for k, v in L.items():
        if 'impl-1278835828/main' in k: A['logs']['pipe-train'] = log_to_lines(v, head=6, tail=24)
        if 'fraud-detector-predictor' in k and 'kserve' in k: A['logs']['fraud-serve'] = log_to_lines(v, tail=14)
    ml = raw['rest'].get('mlflow', {})
    A['mlflow']['experiments'] = [trim_runs(e) for e in ml.get('experiments', []) if e['meta']['name'] == 'revassurance-fraud']
    A['registry'] = [x for x in raw['rest'].get('model_registry', []) if x['m']['name'] == 'revassurance-fraud-brf']
    A['jobsTimeline'] = [{'name':'make-dspa-bucket','status':'Succeeded','after':2},{'name':'import-fraud-pipeline','status':'Succeeded','after':4},
        {'name':'stage-fraud-model','status':'Succeeded','after':5},{'name':'fraud-infer-smoke','status':'Succeeded','after':7},
        {'name':'fraud-triage','status':'Succeeded','after':8}]
    D = '202-fraud-triage/deploy/ocp/rome'
    steps = [
      mk_step('202', S, 1, [
        C("oc get secret minio-root -n minio -o json | jq '{...accesskey/secretkey remap...}' | oc apply -f -", 'secret/dspa-minio-creds created')],
        'resources', 'The DSPA authenticates to its object store with a project-local Secret; secrets are runtime wiring, never source code.', True),
      mk_step('202', S, 2, [
        C(f'oc apply -f {D}/rbac.yaml', 'role.rbac.authorization.k8s.io/dspa-api-access created', 'rolebinding.rbac.authorization.k8s.io/default-dspa-api-access created', 'rolebinding.rbac.authorization.k8s.io/pipeline-runner-mlflow created'),
        C(f'oc apply -f {D}/job-make-bucket.yaml', 'job.batch/make-dspa-bucket created'),
        C('oc logs -f job/make-dspa-bucket -n agent-school', 'Added `m` successfully.', 'Bucket created successfully `m/dspa-agent-school`.', 'BUCKET_OK')],
        'resources', 'Two authorization facts established before anything runs: KFP API access for the import Job, MLflow access for pipeline workers.', True),
      mk_step('202', S, 3, [
        C(f'oc apply -f {D}/dspa.yaml', 'datasciencepipelinesapplication.datasciencepipelinesapplications.opendatahub.io/dspa created'),
        C('oc get dspa dspa -n agent-school -w',
          'NAME   READY', 'dspa   False   (deploying api-server, persistence agent, workflow controller, mariadb...)', 'dspa   True')],
        'pipelines', 'A complete project-scoped pipeline stack. WebhookReady=False / ManagedPipelineValid=False with reason NotApplicable are EA cosmetics.', True,
        reveal=['dspa']),
      mk_step('202', S, 4, [
        C("oc create configmap fraud-pipeline-src -n agent-school \\\n    --from-file=fraud_train_pipeline.py=202-fraud-triage/pipeline/fraud_train_pipeline.py \\\n    --from-file=import_and_run.py=202-fraud-triage/pipeline/import_and_run.py", 'configmap/fraud-pipeline-src created'),
        C(f'oc apply -f {D}/job-import-pipeline.yaml', 'job.batch/import-fraud-pipeline created'),
        C('oc logs -f job/import-fraud-pipeline -n agent-school',
          'pipeline 897b1275-75dc-475c-9220-7e6dc8299d2f version 9c86db04', 'Run details: .../runs/details/1d7f42b9-f559-4e00-be73-ff876ad064a6', 'RUN_ID 1d7f42b9-f559-4e00-be73-ff876ad064a6'),
        C('oc logs -f pod/fraud-brf-training-bkcxf-...-train (one pipeline step, recorded)', log='pipe-train')],
        'pipelines', 'Compiled, uploaded, and started in-cluster. The run goes green 11/11; the register step prints fraud precision/recall near 0.996/0.999.', True,
        reveal=['pipelineRuns']),
      mk_step('202', S, 5, [
        C("oc create configmap fraud-stage-src -n agent-school --from-file=stage_model.py=202-fraud-triage/serving/stage_model.py", 'configmap/fraud-stage-src created'),
        C(f'oc apply -f {D}/job-stage-model.yaml', 'job.batch/stage-fraud-model created'),
        C('oc logs -f job/stage-fraud-model -n agent-school',
          'up revassurance-fraud-brf/1/MLmodel', 'up revassurance-fraud-brf/1/python_env.yaml', 'up revassurance-fraud-brf/1/python_model.pkl',
          'up revassurance-fraud-brf/1/registered_model_meta', 'up revassurance-fraud-brf/1/requirements.txt', 'UPLOADED 7')],
        'registry', 'The registry-to-object-store bridge: which bytes is production running now has a one-line answer.', True),
      mk_step('202', S, 6, [
        C("oc get secret dspa-minio-creds -o json | jq '{...KServe data connection...}' | oc apply -f -", 'secret/aws-connection-minio-models created'),
        C(f'oc apply -f {D}/serving.yaml', 'serviceaccount/fraud-serving created', 'servingruntime.serving.kserve.io/fraud-mlserver created', 'inferenceservice.serving.kserve.io/fraud-detector created'),
        C('oc logs fraud-detector-predictor-... -c kserve-container (recorded)', log='fraud-serve'),
        C('oc get inferenceservice fraud-detector -n agent-school', 'NAME             READY', 'fraud-detector   True')],
        'deployments', 'MLServer on stock UBI9 speaking the V2 protocol; MLSERVER_PARALLEL_WORKERS=0 and the headless :8080 rule are the EA findings that make it work.', True,
        reveal=['fraud-isvc']),
      mk_step('202', S, 7, [
        C("oc create configmap fraud-infer-smoke-src -n agent-school --from-file=infer_smoke.py=202-fraud-triage/serving/infer_smoke.py", 'configmap/fraud-infer-smoke-src created'),
        C(f'oc apply -f {D}/job-infer-smoke.yaml', 'job.batch/fraud-infer-smoke created'),
        C('oc logs -f job/fraud-infer-smoke -n agent-school',
          'row 118422  true=fraud     fraud_probability=0.98  fraud_flag=1', 'row 240187  true=legit     fraud_probability=0.02  fraud_flag=0',
          'row 771603  true=legit     fraud_probability=0.01  fraud_flag=0', 'V2 smoke: predictions match labels')],
        'jobs', 'Proof, not vibes: real dataset rows through the live V2 endpoint, served predictions next to true labels.', True),
      mk_step('202', S, 8, [
        C("oc create configmap triage-agent-src -n agent-school --from-file=triage_agent.py=202-fraud-triage/agent/triage_agent.py", 'configmap/triage-agent-src created'),
        C(f'oc apply -f {D}/job-triage.yaml   # run 1: no APPROVE_TOKEN', 'job.batch/fraud-triage created'),
        C('oc logs -f job/fraud-triage -n agent-school', '[case 0cb8c8c5] score=0.97 -> escalate -> LangGraph interrupt -> PARKED',
          'TRIAGE_OK {"clear": 4, "awaiting_approval": 2}'),
        C(f'oc delete job fraud-triage && oc apply -f {D}/job-triage.yaml   # run 2: APPROVE_TOKEN set', 'job.batch/fraud-triage created'),
        C('oc logs -f job/fraud-triage -n agent-school', '[case 0cb8c8c5] resumed by revass-oncall@agent-school -> escalated',
          'TRIAGE_OK {"clear": 4, "escalated": 2}')],
        'experiments', 'The gate proven both directions: parked without a token, resumed with the approver identity. Both episodes audited in Experiments.', True)]
    return A, steps

# ---------------------------------------------------------------- 301
def build_301(raw, S):
    ns = 'agent-school'
    A = {'resources': {}, 'logs': {}, 'mlflow': {}, 'registry': [], 'jobsTimeline': []}
    add_kimi(raw, A)
    for key, kind, pref, nns in [
        ('loop-state','deployments','loop-state',ns),('mcp-playbook','deployments','mcp-playbook',ns),
        ('llama-stack','deployments','llama-stack',ns),('diag','deployments','diagnostic-agent',ns),
        ('plan','deployments','planning-agent',ns),('valid','deployments','validation-agent',ns),
        ('exec','deployments','execution-agent',ns),('judge','deployments','plan-judge-agent',ns),
        ('scorer','deployments','risk-scorer-mcp',ns),('think-tank','deployments','think-tank','think-tank')]:
        it = find(raw, nns, kind, pref)
        if it: A['resources'][key] = slim_dep(it)
    isvc = find(raw, ns, 'inferenceservices', 'netops-remediation-risk')
    if isvc: A['resources']['risk-isvc'] = isvc_slim(isvc)
    gw = find(raw, ns, 'gateways', 'netops-gateway')
    if gw: A['resources']['gateway'] = {'kind':'Gateway','metadata':{'name':'netops-gateway','namespace':ns},'spec':{},'status':{}}
    for apname in ('mcp-playbook-authn','plan-scorer-authn'):
        ap = find(raw, ns, 'authpolicies', apname)
        if ap: A['resources']['ap-'+apname] = {'kind':'AuthPolicy','metadata':{'name':apname,'namespace':ns},'spec':{},
            'status':{'enforced': any(c.get('type')=='Enforced' and c.get('status')=='True' for c in (ap.get('status') or {}).get('conditions',[]))}}
    L = raw['logs']
    for k, v in L.items():
        if 'train-remediation-risk' in k: A['logs']['train'] = log_to_lines(v, head=8, tail=22)
        if 'stage-remediation-risk' in k: A['logs']['stage'] = log_to_lines(v, tail=9)
        if 'smoke-chain' in k: A['logs']['chain'] = log_to_lines(v, tail=22)
        if 'planning-agent' in k: A['logs']['planning'] = log_to_lines(v, tail=12)
    ml = raw['rest'].get('mlflow', {})
    A['mlflow']['experiments'] = [trim_runs(e) for e in ml.get('experiments', []) if e['meta']['name'] == '301-closed-loop']
    A['registry'] = [x for x in raw['rest'].get('model_registry', []) if x['m']['name'] == 'netops-remediation-risk']
    A['jobsTimeline'] = [{'name':'train-remediation-risk','status':'Succeeded','after':5},{'name':'stage-remediation-risk','status':'Succeeded','after':5},{'name':'smoke-chain','status':'Succeeded','after':7}]
    D = '301-closed-loop-netops/deploy/ocp/rome'
    steps = [
      mk_step('301', S, 1, [
        C('oc create secret generic loop-state-auth -n agent-school --from-literal=password="$(openssl rand -hex 16)"', 'secret/loop-state-auth created'),
        C('oc create secret generic llm-credentials -n think-tank --from-literal=LLM_BASE_URL=http://kimi-linear-48b-a3b-predictor.telco-aix.svc.cluster.local:8080/v1 ...', 'secret/llm-credentials created')],
        'resources', 'The namespaces ARE the architecture: agents, actuation target, and external reasoner in three trust domains that share no objects.', True),
      mk_step('301', S, 2, [
        C('oc create configmap diagnostic-agent-src -n agent-school --from-file=agent.py=301-closed-loop-netops/agents/diagnostic/agent.py', 'configmap/diagnostic-agent-src created'),
        C('oc create configmap nf-playbooks -n agent-school --from-file=301-closed-loop-netops/agents/execution/playbooks/{scale_amf,restart_smf,rebalance_upf,rollback}.yml', 'configmap/nf-playbooks created'),
        C('... 14 more ConfigMaps (planning/validation/execution/judge/scorer/think-tank/smokes/train/stage/llama-stack; see MANUAL Step 2)', '16 ConfigMaps created')],
        'resources', 'Stock UBI9 + source in ConfigMaps: what runs is the text you can read. nf-playbooks is the ONLY action set Execution can ever take.', True),
      mk_step('301', S, 3, [
        C(f'oc apply -f {D}/state-store.yaml', 'deployment.apps/loop-state created', 'service/loop-state created'),
        C(f'oc apply -f {D}/fiveg-core.yaml -f {D}/execution-rbac.yaml -f {D}/execution.yaml', 'deployment.apps/amf smf upf created (ns fiveg-core)', 'role/nf-actuator + rolebinding created', 'deployment.apps/execution-agent created'),
        C(f'oc apply -f {D}/think-tank.yaml -f {D}/netops-gateway.yaml -f {D}/mcp-playbook.yaml -f {D}/mcp-gateway-policies.yaml', 'deployment.apps/think-tank created (ns think-tank)', 'gateway.gateway.networking.k8s.io/netops-gateway created', 'deployment.apps/mcp-playbook + HTTPRoute + AuthPolicy + RateLimitPolicy created'),
        C('oc apply -f 302-energy-optimizer/deploy/ocp/rome/llama-stack.yaml', 'deployment.apps/llama-stack created', 'service/llama-stack created')],
        'resources', 'Each file is an architectural commitment: externalized state, scoped actuation, external reasoning, one governed route to network power.', True,
        reveal=['loop-state','exec','think-tank','gateway','mcp-playbook','llama-stack','ap-mcp-playbook-authn'],
        check={'asset':'loop-state','path':'status.availableReplicas','equals':1}),
      mk_step('301', S, 4, [
        C(f'oc apply -f {D}/diagnostic.yaml -f {D}/planning.yaml -f {D}/validation.yaml -f {D}/judge.yaml', 'deployment.apps/diagnostic-agent created', 'deployment.apps/planning-agent created', 'deployment.apps/validation-agent created', 'deployment.apps/plan-judge-agent created'),
        C('oc get pods -n agent-school -l app.kubernetes.io/part-of=agent-school | grep agent',
          'diagnostic-agent-...   1/1  Running', 'planning-agent-...     1/1  Running', 'validation-agent-...   1/1  Running', 'execution-agent-...    1/1  Running', 'plan-judge-agent-...   1/1  Running')],
        'resources', 'Five A2A servers under five ServiceAccounts. The env blocks ARE the wiring diagram: state store, think-tank URL, governed /plan-score route, thresholds.', True,
        reveal=['diag','plan','valid','judge'], check={'asset':'plan','path':'status.availableReplicas','equals':1}),
      mk_step('301', S, 5, [
        C('tar -czf /tmp/remdata.tgz -C 101-noc-assistant/data amf_metrics.csv smf_metrics.csv upf_metrics.csv alerts.json && oc create configmap remediation-train-data -n agent-school --from-file=data.tgz=/tmp/remdata.tgz', 'configmap/remediation-train-data created (gzipped, 376 KB)'),
        C(f'oc apply -f {D}/job-train-risk.yaml && oc logs -f job/train-remediation-risk', log='train'),
        C(f'oc apply -f {D}/job-stage-risk.yaml && oc logs -f job/stage-remediation-risk', log='stage'),
        C(f'oc apply -f {D}/serving.yaml && oc get isvc netops-remediation-risk -w', 'servingruntime/remediation-mlserver + inferenceservice/netops-remediation-risk created', 'netops-remediation-risk   True')],
        'registry', 'The quantitative half of the co-decision: r2 0.9714 on real KPis, registered v1, staged to MinIO, served on KServe.', False,
        reveal=['risk-isvc']),
      mk_step('301', S, 6, [
        C(f'oc apply -f {D}/scorer-mcp.yaml', 'serviceaccount/risk-scorer-tool created', 'deployment.apps/risk-scorer-mcp created', 'service/risk-scorer-mcp created'),
        C(f'oc apply -f {D}/scorer-gateway-policies.yaml', 'httproute.gateway.networking.k8s.io/plan-score-route created', 'authpolicy.kuadrant.io/plan-scorer-authn created', 'ratelimitpolicy.kuadrant.io/plan-scorer-ratelimit created'),
        C('oc get authpolicy plan-scorer-authn -o jsonpath="{.status.conditions}"', 'Accepted=True  Enforced=True   (only planning-agent and plan-judge-agent may call /plan-score)')],
        'deployments', 'The risk model becomes a governed tool: every plan score passes a policy enforcement point with an identity attached.', True,
        reveal=['scorer','ap-plan-scorer-authn']),
      mk_step('301', S, 7, [
        C(f'oc apply -f {D}/job-smoke-chain.yaml', 'job.batch/smoke-chain created'),
        C('oc logs -f job/smoke-chain -n agent-school', log='chain')],
        'experiments', 'Diagnostic read the store, Planning consulted the think-tank, scored via /plan-score, co-decided, stored the plan. Clean telemetry, honest no-action plan, CHAIN_OK.', False)]
    return A, steps

# ---------------------------------------------------------------- 302
def build_302(raw, S):
    ns = 'agent-school'
    A = {'resources': {}, 'logs': {}, 'mlflow': {}, 'registry': [], 'jobsTimeline': []}
    add_kimi(raw, A)
    for key, pref in [('scorer-mcp','scorer-mcp'),('judge','judge-agent')]:
        it = find(raw, ns, 'deployments', pref)
        if it: A['resources'][key] = slim_dep(it)
    isvc = find(raw, ns, 'inferenceservices', 'sustainability-scorer')
    if isvc: A['resources']['sust-isvc'] = isvc_slim(isvc)
    sa = find(raw, ns, 'serviceaccounts', 'energy-optimizer')
    if sa: A['resources']['sa-opt'] = slim(sa, False)
    ap = find(raw, ns, 'authpolicies', 'scorer-authn')
    if ap: A['resources']['ap-scorer-authn'] = {'kind':'AuthPolicy','metadata':{'name':'scorer-authn','namespace':ns},'spec':{},
        'status':{'enforced': any(c.get('type')=='Enforced' and c.get('status')=='True' for c in (ap.get('status') or {}).get('conditions',[]))}}
    L = raw['logs']
    for k, v in L.items():
        if 'train-sustainability' in k: A['logs']['train'] = log_to_lines(v, head=8, tail=20)
        if 'stage-sustainability' in k: A['logs']['stage'] = log_to_lines(v, tail=9)
        if 'optimize-episode' in k: A['logs']['optimize'] = log_to_lines(v, head=8, tail=26)
        if 'sim-556' in k: A['logs']['sim'] = log_to_lines(v, tail=12)
        if 'genai-eval' in k: A['logs']['eval'] = log_to_lines(v, head=6, tail=22)
    ml = raw['rest'].get('mlflow', {})
    A['mlflow']['experiments'] = [trim_runs(e) for e in ml.get('experiments', []) if e['meta']['name'] == '302-energy-optimizer']
    A['registry'] = [x for x in raw['rest'].get('model_registry', []) if x['m']['name'] == 'sustainability-energy-efficiency']
    A['jobsTimeline'] = [{'name':'train-sustainability','status':'Succeeded','after':2},{'name':'stage-sustainability','status':'Succeeded','after':2},
        {'name':'optimize-episode','status':'Succeeded','after':5},{'name':'cell-sleep-sim (agent-submitted)','status':'Succeeded','after':5},
        {'name':'genai-eval','status':'Succeeded','after':6}]
    D = '302-energy-optimizer/deploy/ocp/rome'
    steps = [
      mk_step('302', S, 1, [
        C('oc create configmap optimizer-src -n agent-school --from-file=energy_optimizer.py=302-energy-optimizer/agent/energy_optimizer.py', 'configmap/optimizer-src created'),
        C('... 6 more ConfigMaps (sustain-train/stage, sim, genai-eval, judge, scorer-mcp; see MANUAL Step 1)', '7 ConfigMaps created'),
        C(f'oc apply -f {D}/sim-rbac.yaml', 'serviceaccount/energy-optimizer created', 'role.rbac.authorization.k8s.io/sim-job-submitter created', 'rolebinding.rbac.authorization.k8s.io/energy-optimizer-sim-submitter created')],
        'resources', 'The narrowest possible identity for an agent that creates workloads: Jobs + pod logs in this one namespace, nothing else.', True,
        reveal=['sa-opt']),
      mk_step('302', S, 2, [
        C(f'oc apply -f {D}/job-train-scorer.yaml && oc logs -f job/train-sustainability', log='train'),
        C('oc logs -f job/stage-sustainability -n agent-school', log='stage')],
        'registry', 'A deliberately simple scorer: the value is that the number the agent optimizes against is versioned, registered, and served.', False),
      mk_step('302', S, 3, [
        C(f'oc apply -f {D}/serving.yaml', 'servingruntime.serving.kserve.io/sustainability-mlserver created', 'inferenceservice.serving.kserve.io/sustainability-scorer created'),
        C(f'oc apply -f {D}/scorer-mcp.yaml -f {D}/scorer-gateway-policies.yaml', 'deployment.apps/scorer-mcp + service created', 'httproute/scorer-route + authpolicy/scorer-authn + ratelimitpolicy/scorer-ratelimit created'),
        C('oc get isvc sustainability-scorer; oc get authpolicy scorer-authn', 'sustainability-scorer   True', 'scorer-authn: Accepted=True Enforced=True (energy-optimizer + judge-agent only)')],
        'deployments', 'The doer and the checker measure with the same governed instrument: /score admits exactly two identities.', True,
        reveal=['sust-isvc','scorer-mcp','ap-scorer-authn']),
      mk_step('302', S, 4, [
        C(f'oc apply -f {D}/judge.yaml', 'serviceaccount/judge-agent created', 'deployment.apps/judge-agent created', 'service/judge-agent created'),
        C('oc rollout status deploy/judge-agent -n agent-school', 'deployment "judge-agent" successfully rolled out')],
        'resources', 'One more A2A agent, harnessed through Llama Stack over the same in-cluster Kimi model.', True,
        reveal=['judge'], check={'asset':'judge','path':'status.availableReplicas','equals':1}),
      mk_step('302', S, 5, [
        C(f'oc apply -f {D}/job-optimize.yaml', 'job.batch/optimize-episode created'),
        C('oc logs -f job/optimize-episode -n agent-school', log='optimize'),
        C('oc logs job/sim-556f27e635-r1 -n agent-school   # the sim Job the agent submitted', log='sim')],
        'experiments', 'Simulate before acting: the agent submitted its own sim Job under the scoped SA, scored the outcome via /score, and accepted in round 1.', False),
      mk_step('302', S, 6, [
        C(f'oc apply -f {D}/job-genai-eval.yaml', 'job.batch/genai-eval created'),
        C('oc logs -f job/genai-eval -n agent-school', log='eval')],
        'experiments', 'Agents grading agents, with the grades stored as platform data: decision correctness, numeric groundedness, QoS safety, LLM groundedness.', False)]
    return A, steps

BUILDERS = {'101': build_101, '201': build_201, '202': build_202, '301': build_301, '302': build_302}

def main():
    course, cluster, raw_path, manual_path, out_path = sys.argv[1:6]
    raw = load_raw(raw_path)
    manual_steps = parse_manual(manual_path)
    assets, steps = BUILDERS[course](raw, manual_steps)
    tape = {'tapeVersion': 1, 'course': course, 'title': {'101':'NOC Assistant','201':'RCA Investigator','202':'Fraud Triage','301':'Closed-Loop NetOps','302':'Energy Optimizer'}[course],
            'cluster': cluster, 'capturedAt': raw['capturedAt'], 'steps': steps, 'assets': assets}
    s = json.dumps(tape, separators=(',', ':'))
    open(out_path, 'w').write(s)
    print(f"{out_path}: {len(s)//1024} KB, {len(steps)} steps, "
          f"{len(assets['resources'])} resources, {len(assets['logs'])} log streams")

if __name__ == '__main__':
    main()
