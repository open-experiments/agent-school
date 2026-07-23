"""The autonet playbook MCP server — the loop's governed tool boundary.

Track 4 hardens the article's central governance claim: "Execution can
only reach the 5G core through the MCP Gateway and the audited autonet
playbooks." Before Track 4, the Execution agent ran ansible-playbook
itself and held the fiveg-core RBAC directly. Now the playbooks live
here, behind an MCP tool boundary, and this server is the ONLY thing
with the nf-actuator Role. Execution reaches it over MCP through a
Kuadrant-governed Gateway (AuthPolicy verifies the caller; a
RateLimitPolicy caps actuation) — deploy/ocp/rome/netops-gateway.yaml.

Real MCP (streamable-HTTP, python-sdk 1.28.x, stateless). Tools:
  run_playbook(playbook)  — run one governed autonet playbook against
                            fiveg-core (catalog-enforced in code); the
                            same real kubernetes.core actuation the
                            Execution agent used to do, now isolated
                            here with the RBAC that permits it.
  nf_state()              — read-only post-action view of the NFs.

The governance is layered: the gateway decides WHO may call and HOW
OFTEN (Kuadrant); this server decides WHAT may run (the catalog); and
the pod's ServiceAccount is the only credential that can touch
fiveg-core (RBAC). Compromising the agent no longer grants actuation —
it only grants the ability to ask, through the gate.

Runs as deploy/ocp/rome/mcp-playbook.yaml.
"""
import json
import os
import subprocess
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

SA_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"
KUBECONFIG = "/tmp/kubeconfig"
PLAYBOOK_DIR = os.environ.get("PLAYBOOK_DIR", "/playbooks")
CATALOG = {"scale_amf", "restart_smf", "rebalance_upf", "rollback"}


def write_kubeconfig():
    kc = {
        "apiVersion": "v1", "kind": "Config",
        "clusters": [{"name": "in", "cluster": {
            "server": "https://kubernetes.default.svc",
            "certificate-authority": SA_DIR + "/ca.crt"}}],
        "users": [{"name": "sa", "user": {"tokenFile": SA_DIR + "/token"}}],
        "contexts": [{"name": "in", "context": {
            "cluster": "in", "user": "sa"}}],
        "current-context": "in",
    }
    Path(KUBECONFIG).write_text(json.dumps(kc))


write_kubeconfig()

srv = FastMCP(
    "autonet-playbooks",
    instructions="Governed actuation for the 5G core (fiveg-core). "
                 "Runs only the audited autonet playbook catalog.",
    host="0.0.0.0", port=int(os.environ.get("PORT", "8080")),
    stateless_http=True)


def nf_snapshot():
    try:
        from kubernetes import client, config
        config.load_incluster_config()
        apps = client.AppsV1Api()
        out = {}
        for d in apps.list_namespaced_deployment("fiveg-core").items:
            ann = (d.spec.template.metadata.annotations or {})
            out[d.metadata.name] = {
                "replicas": d.spec.replicas,
                "ready": d.status.ready_replicas,
                "restartedAt": ann.get("loop.agent-school/restartedAt"),
                "rebalancedAt": ann.get("loop.agent-school/rebalancedAt")}
        return out
    except Exception as e:
        return {"error": type(e).__name__ + ": " + str(e)[:150]}


@srv.tool()
def run_playbook(playbook: str) -> str:
    """Run one governed autonet playbook (scale_amf | restart_smf |
    rebalance_upf | rollback) against the fiveg-core NFs. Returns JSON
    with rc, output tail, and the post-action NF snapshot."""
    if playbook not in CATALOG:
        return json.dumps({"playbook": playbook, "rc": -1,
                           "result": "REFUSED: not in governed catalog"})
    env = dict(os.environ, HOME="/tmp", K8S_AUTH_KUBECONFIG=KUBECONFIG,
               ANSIBLE_LOCAL_TEMP="/tmp/.ansible/tmp",
               ANSIBLE_COLLECTIONS_PATH="/tmp/.ansible/collections",
               ANSIBLE_STDOUT_CALLBACK="oneline")
    t0 = time.time()
    proc = subprocess.run(
        ["ansible-playbook",
         os.path.join(PLAYBOOK_DIR, playbook + ".yml")],
        capture_output=True, text=True, timeout=420, env=env)
    tail = (proc.stdout + proc.stderr).strip().splitlines()[-6:]
    return json.dumps({"playbook": playbook, "rc": proc.returncode,
                       "seconds": round(time.time() - t0, 1),
                       "output_tail": tail,
                       "nf_state_after": nf_snapshot()})


@srv.tool()
def nf_state() -> str:
    """Read-only snapshot of the fiveg-core NFs (replicas + loop
    annotations)."""
    return json.dumps(nf_snapshot())


if __name__ == "__main__":
    srv.run(transport="streamable-http")
