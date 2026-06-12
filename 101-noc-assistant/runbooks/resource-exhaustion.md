# Runbook: Resource Exhaustion (AMF/SMF/UPF)

**Alert type:** `RESOURCE_EXHAUSTION` · **Typical severity:** CRITICAL / MAJOR

CPU, memory, or buffer pressure on a network function. On the UPF this shows
as cpu_utilization and buffer_utilization climbing while throughput_mbps
drops and packet_drop_rate and latency_ms rise. On AMF/SMF it shows as
cpu/memory growth with success-rate erosion.

## Triage

1. Identify which resource is exhausted: cpu_utilization, memory_utilization,
   or (UPF) buffer_utilization.
2. Check whether the pressure is load-driven (packet_processing_rate or
   message rates also up) or leak-driven (load flat, memory climbing).
3. Review the alert feed deltas to bound the impact window.

## Remediation

1. Load-driven: scale out the affected NF; for UPF, rebalance sessions
   across UPF instances before scaling.
2. Leak-driven: restart the affected pod during a maintenance window and
   capture a heap/profile snapshot first.
3. Re-apply QoS admission thresholds if qos_flow_success_rate degraded.

## Validation

Utilization back under 70 percent; packet_drop_rate under 0.2 percent;
latency_ms within 20 percent of baseline for 30 consecutive minutes.
