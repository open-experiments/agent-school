{
  "Incident": "Registration storm cascading into session-management and resource-exhaustion failures across AMF, SMF and UPF",
  "Evidence": [
    {
      "alert_id": "alert-0",
      "component": "AMF",
      "type": "REGISTRATION_STORM",
      "severity": "CRITICAL",
      "window": "2025-01-16 16:37:55.775630..2025-01-16 16:42:55.775630",
      "kpi_delta": {"cpu_utilization": -7.98, "authentication_success_rate": -11.73}
    },
    {
      "alert_id": "alert-1",
      "component": "SMF",
      "type": "REGISTRATION_STORM",
      "severity": "CRITICAL",
      "window": "2025-01-17 07:12:55.775630..2025-01-17 07:17:55.775630",
      "kpi_delta": {"session_establishment_rate": -17.80}
    },
    {
      "alert_id": "alert-3",
      "component": "SMF",
      "type": "RESOURCE_EXHAUSTION",
      "severity": "MAJOR",
      "window": "2025-01-16 12:22:55.775630..2025-01-16 12:27:55.775630",
      "kpi_delta": {"session_establishment_rate": -15.02, "n4_message_rate": -17.67}
    },
    {
      "alert_id": "alert-4",
      "component": "SMF",
      "type": "SESSION_MANAGEMENT_FAILURE",
      "severity": "MAJOR",
      "window": "2025-01-16 22:29:55.775630..2025-01-16 22:34:55.775630",
      "kpi_delta": {"cpu_utilization": -5.92, "session_establishment_rate": -23.73, "n4_message_rate": -20.90}
    },
    {
      "alert_id": "alert-5",
      "component": "AMF",
      "type": "SESSION_MANAGEMENT_FAILURE",
      "severity": "MAJOR",
      "window": "2025-01-17 00:47:55.775630..2025-01-17 00:52:55.775630",
      "kpi_delta": {"session_setup_rate": -18.21}
    },
    {
      "alert_id": "alert-6",
      "component": "UPF",
      "type": "SESSION_MANAGEMENT_FAILURE",
      "severity": "MAJOR",
      "window": "2025-01-17 06:37:55.775630..2025-01-17 06:42:55.775630",
      "kpi_delta": {"tunnel_establishment_rate": -15.52}
    },
    {
      "alert_id": "alert-1",
      "component": "SMF",
      "type": "REGISTRATION_STORM",
      "severity": "CRITICAL",
      "window": "2025-01-17 07:12:55.775630..2025-01-17 07:17:55.775630",
      "kpi_delta": {"session_establishment_rate": -17.80}
    }
  ],
  "Root cause": "A surge in registration requests (registration storm) overwhelmed the AMF and SMF, leading to CPU and session-management resource exhaustion and subsequent service degradation across AMF, SMF and UPF.",
  "Contributing factors": [
    "Registration storm first observed on AMF at 2025-01-16 16:37:55 [alert-0]",
    "Registration storm propagated to SMF at 2025-01-17 07:12:55 [alert-1]",
    "Resource-exhaustion on SMF at 2025-01-16 12:22:55 due to UPF resource saturation [alert-3]",
    "Session-management failures on SMF at 2025-01-16 22:29:55 with -23.7% session-establishment-rate drop [alert-4]",
    "Session-management failures on AMF at 2025-01-17 00:47:55 with -18.2% session-setup-rate drop [alert-5]",
    "Session-management failures on UPF at 2025-01-17 06:37:55 with -15.5% tunnel-establishment-rate drop [alert-6]"
  ],
  "Recommended remediation": [
    "Investigate and mitigate the root cause of the registration storm (e.g., faulty UE, mis-configured load, or signalling attack) to prevent recurrence.",
    "Increase AMF/SMF capacity or enable back-pressure/throttling during high registration rates to avoid CPU and session-management saturation.",
    "Implement early-warning KPI thresholds and automated scaling or admission control to limit registration-rate spikes.",
    "Review and harden N1/N2/N4 signalling flows to detect and drop malformed or excessive registration requests at the edge nodes.",
    "Establish run-books for rapid isolation of affected components and rollback procedures during registration-storm events."
  ]
}