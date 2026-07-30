#!/usr/bin/env bash
# Remove the agent-school student accounts from the Venice cluster —
# the exact inverse of setup-students.sh:
#
#   RoleBinding + ResourceQuota + LimitRange in agent-school,
#   group, users, identities, oauth tokens, the htpasswd secret,
#   and the agent-school-students IdP entry in the cluster OAuth.
#
# Run logged in as a cluster admin (kubeadmin):
#   ./cleanup-students.sh          # asks for confirmation
#   ./cleanup-students.sh --yes    # non-interactive
#
# Idempotent and tolerant: anything already gone is skipped.
set -euo pipefail
cd "$(dirname "$0")"

NS=agent-school
GROUP=agent-school-students
IDP_NAME=agent-school-students
SECRET=agent-school-students-htpasswd
USERS=(student1 student2 student3 student4 student5)
CREDS=student-credentials.txt

oc whoami >/dev/null 2>&1 || { echo "FATAL: not logged in (oc login as kubeadmin first)"; exit 1; }
oc auth can-i patch oauth.config.openshift.io/cluster >/dev/null 2>&1 || { echo "FATAL: $(oc whoami) cannot patch the cluster OAuth config — log in as kubeadmin"; exit 1; }

if [ "${1:-}" != "--yes" ]; then
  echo "This removes users ${USERS[*]}, their group, RBAC, quota guardrails,"
  echo "the htpasswd secret, and the '$IDP_NAME' IdP from the Venice OAuth config."
  printf "Type 'yes' to continue: "
  read -r answer
  [ "$answer" = "yes" ] || { echo "Aborted."; exit 1; }
fi

# --- 1. revoke active sessions (best-effort) ------------------------------
for u in "${USERS[@]}"; do
  tokens=$(oc get oauthaccesstokens -o jsonpath='{range .items[?(@.userName=="'"$u"'")]}{.metadata.name}{" "}{end}' 2>/dev/null || true)
  for t in $tokens; do oc delete oauthaccesstoken "$t" >/dev/null 2>&1 || true; done
done
echo "OK  active oauth tokens revoked"

# --- 2. namespace objects --------------------------------------------------
oc delete rolebinding agent-school-students-edit -n "$NS" --ignore-not-found
oc delete resourcequota agent-school-guardrail   -n "$NS" --ignore-not-found
oc delete limitrange agent-school-defaults       -n "$NS" --ignore-not-found
echo "OK  RoleBinding + ResourceQuota + LimitRange removed from $NS"

# --- 3. group, users, identities ------------------------------------------
oc delete group "$GROUP" --ignore-not-found
for u in "${USERS[@]}"; do
  oc delete user "$u" --ignore-not-found
  oc delete identity "$IDP_NAME:$u" --ignore-not-found
done
echo "OK  group, users, identities removed"

# --- 3b. dashboard extras (registry, kueue read, serving-ns view) ---------
for crd in modelregistries.modelregistry.opendatahub.io modelregistries.components.platform.opendatahub.io; do
  if oc get crd "$crd" >/dev/null 2>&1; then
    while read -r ns name; do
      [ -z "${name:-}" ] && continue
      oc delete rolebinding "${GROUP}-${name}" -n "$ns" --ignore-not-found
    done < <(oc get "$crd" -A -o jsonpath='{range .items[*]}{.metadata.namespace}{" "}{.metadata.name}{"\n"}{end}')
  fi
done
oc delete clusterrolebinding agent-school-students-kueue-reader --ignore-not-found
oc delete clusterrole agent-school-kueue-reader --ignore-not-found
oc delete rolebinding agent-school-students-view -n telco-aix --ignore-not-found
echo "OK  dashboard extras removed (registry, kueue read, serving-ns view)"

# --- 4. IdP entry out of the cluster OAuth --------------------------------
names_str=$(oc get oauth cluster -o jsonpath='{.spec.identityProviders[*].name}')
if [[ " $names_str " == *" $IDP_NAME "* ]]; then
  read -r -a names <<< "$names_str"
  idx=-1
  for i in "${!names[@]}"; do [ "${names[$i]}" = "$IDP_NAME" ] && idx=$i; done
  oc patch oauth cluster --type=json -p '[{"op":"remove","path":"/spec/identityProviders/'"$idx"'"}]'
  echo "OK  IdP '$IDP_NAME' removed from OAuth (oauth pods roll for ~1-2 min)"
else
  echo "OK  IdP '$IDP_NAME' not present (already removed)"
fi

# --- 5. htpasswd secret + local credentials file --------------------------
oc delete secret "$SECRET" -n openshift-config --ignore-not-found
echo "OK  secret openshift-config/$SECRET removed"
if [ -f "$CREDS" ]; then rm -f "$CREDS"; echo "OK  local $CREDS deleted"; fi

# --- 6. sanity -------------------------------------------------------------
echo
echo "--- sanity: student1 should now be denied everywhere ---"
oc auth can-i create pods -n "$NS" --as=student1 || true
echo
echo "DONE. If you had also removed self-provisioners and want it back:"
echo "  oc patch clusterrolebinding.rbac self-provisioners -p '{\"subjects\":[{\"apiGroup\":\"rbac.authorization.k8s.io\",\"kind\":\"Group\",\"name\":\"system:authenticated:oauth\"}]}'"
