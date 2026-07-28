#!/usr/bin/env python3
"""Build course tapes for the Agent School portal from raw cluster snapshots.

Usage: build-tapes.py <course> <cluster> <raw-tape.json[.gz]> <manual.md> <out.json>

The generator slices the raw snapshot (shared/tapes/*-tape-raw.json.gz) into the
per-course tape format described in shared/tapes/TAPE-SCHEMA.md. Steps mirror the
course MANUAL.md exactly (parsed from it). Terminal playback uses captured pod
logs where the pod survived to capture time; short excerpts reconstructed from
session transcripts are marked "reconstructed".
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

def find(raw, ns, kind, name_prefix):
    for it in raw['resources'].get(ns, {}).get(kind, []):
        if it['metadata']['name'].startswith(name_prefix):
            return it
    return None

# ---------------------------------------------------------------- course configs
def build_101(raw, manual_steps):
    ns = 'agent-school'
    A = {'resources': {}, 'logs': {}, 'mlflow': {}, 'registry': []}
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

BUILDERS = {'101': build_101}

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
