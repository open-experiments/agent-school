# Runbook: Registration Storm (AMF/SMF)

**Alert type:** `REGISTRATION_STORM` · **Typical severity:** CRITICAL

A registration storm is a surge of UE registration attempts, usually after a
site recovery, a misbehaving device fleet, or a paging misconfiguration. The
AMF sees registration_rate and n1n2_message_rate spike while
authentication_success_rate and ngap_success_rate sag; SMF session KPIs can
degrade as the surge propagates.

## Triage

1. Confirm the window: compare registration_rate and n1n2_message_rate
   against the pre-alert baseline (the alert feed carries before/during deltas).
2. Check whether authentication_success_rate dropped more than 5 percent;
   if so the storm is saturating AUSF/UDM paths, not just the AMF.
3. Correlate with SMF session_establishment_rate to see if the storm is
   propagating to session setup.

## Remediation

1. Enable or tighten AMF overload control (N1/N2 back-off timers) so
   re-attempts spread out.
2. If a specific TAC or device fleet dominates, apply registration rate
   limiting for that group.
3. Scale AMF replicas if cpu_utilization stays above 80 percent after
   back-off is in place.

## Validation

Registration_rate returns to baseline within two back-off cycles;
authentication and NGAP success rates recover above 99 percent.
