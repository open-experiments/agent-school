# Runbook: Session Management Failure (SMF/UPF)

**Alert type:** `SESSION_MANAGEMENT_FAILURE` · **Typical severity:** MAJOR

Session establishment or modification failures between SMF and UPF. The SMF
shows session_establishment_rate or session_success_rate dropping with
pfcp_message_rate or n4_message_rate anomalies; the UPF side shows
tunnel_establishment_rate sagging while qos_flow_success_rate degrades.

## Triage

1. Determine the failing leg: SMF-side (PFCP/N4 message anomalies) or
   UPF-side (tunnel establishment failures).
2. Check policy_installation_rate on the SMF; PCF latency can masquerade
   as session failure.
3. Correlate the alert window with any UPF resource exhaustion alert;
   session failures are often a downstream symptom.

## Remediation

1. PFCP path issues: verify N4 association health and restart the PFCP
   association if heartbeats are missing.
2. Policy-driven: clear the policy installation backlog or fail over to
   the secondary PCF.
3. UPF capacity: follow the resource-exhaustion runbook first, then retry
   failed sessions.

## Validation

session_success_rate above 99 percent and tunnel_establishment_rate back to
baseline for 15 consecutive minutes; no new alerts in the feed.
