from prometheus_client import Counter, Gauge, Histogram

COMMANDS_TOTAL = Counter(
    "chatops_commands_total",
    "Total ChatOps commands processed",
    ["action", "environment", "outcome"],
)

OPERATION_DURATION = Histogram(
    "chatops_operation_duration_seconds",
    "Duration of DevOps operations from trigger to completion",
    ["action", "environment"],
    buckets=[5, 15, 30, 60, 120, 300, 600],
)

OPERATIONS_IN_FLIGHT = Gauge(
    "chatops_operations_in_flight",
    "Number of DevOps operations currently executing",
    ["action"],
)

JENKINS_QUEUE_DEPTH = Gauge(
    "chatops_jenkins_queue_depth",
    "Number of builds currently in Jenkins queue",
)

PERMISSION_DENIALS = Counter(
    "chatops_permission_denials_total",
    "Total permission denials",
    ["user_role", "action", "environment"],
)
