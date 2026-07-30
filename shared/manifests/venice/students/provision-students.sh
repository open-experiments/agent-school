#!/usr/bin/env bash
# Provision limited-scope student accounts on the Venice cluster — one shot.
#
#   student1..student5 · htpasswd IdP · `edit` on the agent-school
#   namespace only · ResourceQuota + LimitRange guardrails · read-only
#   dashboard extras (model registry access + Workload metrics page).
#
# Run from this directory, logged in as a cluster admin (kubeadmin):
#   oc login https://api.venice.narlabs.io:6443 -u kubeadmin
#   ./provision-students.sh
#
# Idempotent: re-running refreshes everything without duplicating —
# note that passwords are REGENERATED on every run (rotation), written
# once to ./student-credentials.txt (mode 600, gitignored) and echoed
# at the end. Distribute out-of-band, then delete the file.
#
# Inverse: ./cleanup-students.sh
set -euo pipefail
cd "$(dirname "$0")"

NS=agent-school
GROUP=agent-school-students
IDP_NAME=agent-school-students
SECRET=agent-school-students-htpasswd
USERS=(student1 student2 student3 student4 student5)
CREDS=student-credentials.txt

command -v htpasswd >/dev/null || { echo "FATAL: htpasswd not found (macOS ships it; on Linux install httpd-tools)"; exit 1; }
oc whoami >/dev/null 2>&1 || { echo "FATAL: not logged in (oc login as kubeadmin first)"; exit 1; }
oc auth can-i patch oauth.config.openshift.io/cluster >/dev/null 2>&1 || { echo "FATAL: $(oc whoami) cannot patch the cluster OAuth config — log in as kubeadmin"; exit 1; }
oc get ns "$NS" >/dev/null || { echo "FATAL: namespace $NS not found"; exit 1; }

# === 1. htpasswd file with fresh random passwords =========================
HTFILE=$(mktemp)
trap 'rm -f "$HTFILE"' EXIT
: > "$CREDS"; chmod 600 "$CREDS"
echo "# Venice · agent-school student credentials · generated $(date -u +%Y-%m-%dT%H:%MZ)" >> "$CREDS"
echo "# Console: https://console-openshift-console.apps.venice.narlabs.io  (IdP: $IDP_NAME)" >> "$CREDS"
for u in "${USERS[@]}"; do
  # finite stream first (head), THEN filter — an infinite-source pipe like
  # `tr </dev/urandom | head` dies of SIGPIPE under `set -o pipefail`.
  p=$(head -c 48 /dev/urandom | base64 | LC_ALL=C tr -dc 'A-Za-z0-9' | cut -c1-16)
  htpasswd -B -b "$HTFILE" "$u" "$p" >/dev/null 2>&1
  printf '%s : %s\n' "$u" "$p" >> "$CREDS"
  echo "OK  generated $u"
done
echo "OK  htpasswd file built for: ${USERS[*]}"

# === 2. secret in openshift-config (create or refresh) ====================
oc create secret generic "$SECRET" -n openshift-config \
  --from-file=htpasswd="$HTFILE" \
  --dry-run=client -o yaml | oc apply -f -
echo "OK  secret openshift-config/$SECRET"

# === 3. add the IdP to the cluster OAuth (append once) ====================
EXISTING_IDPS=$(oc get oauth cluster -o jsonpath='{.spec.identityProviders[*].name}')
if [[ " $EXISTING_IDPS " == *" $IDP_NAME "* ]]; then
  echo "OK  OAuth IdP '$IDP_NAME' already present"
else
  IDP_JSON='{"name":"'"$IDP_NAME"'","mappingMethod":"claim","type":"HTPasswd","htpasswd":{"fileData":{"name":"'"$SECRET"'"}}}'
  if [ "$(oc get oauth cluster -o jsonpath='{.spec.identityProviders}')" = "" ]; then
    oc patch oauth cluster --type=json -p '[{"op":"add","path":"/spec/identityProviders","value":['"$IDP_JSON"']}]'
  else
    oc patch oauth cluster --type=json -p '[{"op":"add","path":"/spec/identityProviders/-","value":'"$IDP_JSON"'}]'
  fi
  echo "OK  OAuth IdP '$IDP_NAME' added (oauth pods roll for ~1-2 min before logins work)"
fi

# === 4. group + membership ================================================
oc get group "$GROUP" >/dev/null 2>&1 || oc adm groups new "$GROUP" >/dev/null
oc adm groups add-users "$GROUP" "${USERS[@]}" >/dev/null
echo "OK  group $GROUP: $(oc get group "$GROUP" -o jsonpath='{.users[*]}')"

# === 5. RBAC + guardrails =================================================
oc apply -f rbac.yaml
oc apply -f quota.yaml
echo "OK  RoleBinding (edit on $NS) + ResourceQuota + LimitRange applied"

# === 6. dashboard extras (read-only) ======================================
# 6a. model registries: access = RoleBindings in the model-registries
#     namespace, one registry-user-<name> Role per registry. Discover
#     every ModelRegistry on the cluster and bind the group to each.
FOUND_REG=0
for crd in modelregistries.modelregistry.opendatahub.io modelregistries.components.platform.opendatahub.io; do
  if oc get crd "$crd" >/dev/null 2>&1; then
    while read -r ns name; do
      [ -z "${name:-}" ] && continue
      FOUND_REG=1
      ROLE="registry-user-$name"
      if oc get role "$ROLE" -n "$ns" >/dev/null 2>&1; then
        cat <<EOF | oc apply -f -
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: ${GROUP}-${name}
  namespace: ${ns}
  labels:
    app.kubernetes.io/part-of: agent-school
subjects:
  - kind: Group
    apiGroup: rbac.authorization.k8s.io
    name: ${GROUP}
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: ${ROLE}
EOF
        echo "OK  group bound to model registry '$name' (role $ROLE in $ns)"
      else
        echo "WARN registry '$name' in '$ns' has no role '$ROLE' — grant via dashboard:"
        echo "     Settings -> Model registry settings -> $name -> Manage permissions -> add group $GROUP"
      fi
    done < <(oc get "$crd" -A -o jsonpath='{range .items[*]}{.metadata.namespace}{" "}{.metadata.name}{"\n"}{end}')
  fi
done
[ "$FOUND_REG" = 1 ] || echo "WARN no ModelRegistry resources found on this cluster"

# 6b. Workload metrics page: dashboard gates on read access to Kueue
#     queue objects (clusterqueues are cluster-scoped). Read-only.
cat <<'EOF' | oc apply -f -
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: agent-school-kueue-reader
  labels:
    app.kubernetes.io/part-of: agent-school
rules:
  - apiGroups: ["kueue.x-k8s.io"]
    resources: ["clusterqueues", "localqueues", "workloads", "resourceflavors"]
    verbs: ["get", "list", "watch"]
EOF
cat <<EOF | oc apply -f -
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: agent-school-students-kueue-reader
  labels:
    app.kubernetes.io/part-of: agent-school
subjects:
  - kind: Group
    apiGroup: rbac.authorization.k8s.io
    name: ${GROUP}
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: agent-school-kueue-reader
EOF
echo "OK  kueue read granted to $GROUP (Workload metrics page)"

# 6c. serving namespace: read-only view of the course LLM (Kimi lives in
#     SERVING_NS by design — students can SEE the running model, its
#     status and endpoint, but Stop/Delete stays denied).
SERVING_NS=telco-aix
if oc get ns "$SERVING_NS" >/dev/null 2>&1; then
  cat <<EOF | oc apply -f -
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: agent-school-students-view
  namespace: ${SERVING_NS}
  labels:
    app.kubernetes.io/part-of: agent-school
subjects:
  - kind: Group
    apiGroup: rbac.authorization.k8s.io
    name: ${GROUP}
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: view
EOF
  echo "OK  read-only view on '$SERVING_NS' granted (see the course LLM, cannot stop it)"
else
  echo "WARN serving namespace '$SERVING_NS' not found — skipping view grant"
fi

# === 7. sanity ============================================================
echo
echo "--- sanity (expect: yes / yes / no / no / yes / yes / yes / no) ---"
AS=(--as=student1 --as-group="$GROUP" --as-group='system:authenticated')
oc auth can-i create pods         -n "$NS"             "${AS[@]}" || true
oc auth can-i delete deployments  -n "$NS"             "${AS[@]}" || true
oc auth can-i create namespaces                        "${AS[@]}" || true
oc auth can-i delete pods         -n openshift-config  "${AS[@]}" || true
oc auth can-i list clusterqueues.kueue.x-k8s.io        "${AS[@]}" || true
oc auth can-i list localqueues.kueue.x-k8s.io -n "$NS" "${AS[@]}" || true
oc auth can-i list inferenceservices.serving.kserve.io -n "$SERVING_NS" "${AS[@]}" || true
oc auth can-i delete inferenceservices.serving.kserve.io -n "$SERVING_NS" "${AS[@]}" || true

# === 8. credentials =======================================================
echo
echo "=== student credentials (also saved to ./$CREDS — delete after distributing) ==="
cat "$CREDS"
echo "==============================================================================="
echo
echo "DONE. Console: https://console-openshift-console.apps.venice.narlabs.io (IdP tile: $IDP_NAME)"
echo "Students should (re-)login after ~1-2 min on first setup, or hard-refresh the dashboard."
echo "Optional lockdown (stops students creating NEW projects cluster-wide; affects"
echo "all authenticated users — see README before running):"
echo "  oc patch clusterrolebinding.rbac self-provisioners -p '{\"subjects\":null}'"
