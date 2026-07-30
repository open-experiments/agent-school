# Venice student accounts — limited to `agent-school`

Five sandboxed learner accounts (`student1..student5`) for the Venice
reference cluster: htpasswd logins, the built-in `edit` role bound only
inside the `agent-school` namespace, namespace guardrails so experiments
cannot starve the SNO node, and read-only access to the RHOAI dashboard
areas that live outside namespace RBAC — model registries, the Workload
metrics page, and the serving namespace where the course LLM runs.

| File | Does |
|---|---|
| `provision-students.sh` | One-shot, idempotent provisioning: htpasswd secret → OAuth IdP → group → RBAC → quota → dashboard extras (registry + Kueue read + `view` on `telco-aix`) → sanity probes. Prints fresh passwords (also saved to `student-credentials.txt`, mode 600, gitignored). Re-running rotates all passwords. |
| `cleanup-students.sh` | Full inverse: revokes tokens, removes RBAC/quota/group/users/identities, all extra grants, the htpasswd secret, and the IdP entry. Asks for confirmation; `--yes` for non-interactive. |
| `rbac.yaml` | Group `agent-school-students` → ClusterRole `edit`, RoleBinding scoped to `agent-school`. |
| `quota.yaml` | ResourceQuota (CPU/mem/pods/PVC caps, GPU pinned to the 1 the course's Kimi ISVC already uses) + LimitRange defaults so podspecs without requests still admit. |

## Provision

```bash
oc login https://api.venice.narlabs.io:6443 -u kubeadmin
cd shared/manifests/venice/students
./provision-students.sh
```

Wait ~1–2 minutes after the first run for the oauth pods to roll, then
students log in at the console (IdP tile: **agent-school-students**) or:

```bash
oc login https://api.venice.narlabs.io:6443 -u student1
```

The script ends with eight `oc auth can-i` probes — expect
**yes / yes / no / no / yes / yes / yes / no** (workload rights in
agent-school; no cluster-scoped writes; Kueue read; see-but-not-delete
on the served model) — and the credentials table. Distribute passwords
out-of-band, then delete `student-credentials.txt`. Re-run any time to
rotate passwords.

## What students can and cannot do

Can (inside `agent-school` only): create/delete pods, Deployments, Jobs,
Services, ConfigMaps, PVCs; `oc exec` / `oc logs`; run the course
manifests; see the project in the RHOAI dashboard (the namespace already
carries `opendatahub.io/dashboard=true`). Read-only extras: browse model
registries, the Workload metrics page, and the serving namespace
`telco-aix` — students can watch the course LLM (Kimi) run, see its
endpoint and status, but cannot stop or delete it.

Cannot: write to any other namespace, read Secrets-bearing platform
namespaces, create roles or quotas, see nodes/operators, or exceed the
guardrails (`quota.yaml`; a second GPU request is denied while the
course's Kimi ISVC holds the one budgeted).

Shared-namespace caveat: students share `agent-school` with the course
stack and each other — they *can* delete course workloads there. That is
rebuildable by design (`deploy/` in each course), but if you want the
demos untouchable, switch to a view-plus-personal-sandbox model instead
of `edit`. The course LLM itself is safe: it lives in `telco-aix`, where
students hold only `view`.

Registry note: if a registry exists but lacks its `registry-user-*`
Role, the script prints the supported dashboard path instead —
Settings → Model registry settings → *registry* → Manage permissions →
add group `agent-school-students`.

Dashboard note: if `telco-aix` does not appear in the students' project
picker, label it: `oc label ns telco-aix opendatahub.io/dashboard=true`.

## Optional hardening

By default OpenShift lets any authenticated user create new projects
(self-provisioning), which would let students sprawl outside
`agent-school`. To stop that — note it affects **all** authenticated
users on Venice, not just students:

```bash
oc patch clusterrolebinding.rbac self-provisioners -p '{"subjects":null}'
oc annotate clusterrolebinding.rbac self-provisioners rbac.authorization.kubernetes.io/autoupdate=false --overwrite
```

## Rotate / remove

Rotate passwords: re-run `provision-students.sh`.

Teardown: `./cleanup-students.sh` (asks for confirmation; `--yes` to
skip). It removes everything provisioning created — RBAC, quota, group,
users, identities, active oauth tokens, registry/Kueue/serving-ns
grants, the htpasswd secret, the IdP entry — and deletes any local
`student-credentials.txt`.
