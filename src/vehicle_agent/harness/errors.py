class HarnessError(Exception):
    """Base error for model and tool execution."""

    code = "harness_error"


class BudgetExceededError(HarnessError):
    code = "budget_exceeded"


class ToolExecutionError(HarnessError):
    code = "tool_failed"


class OutputValidationError(HarnessError):
    code = "output_invalid"


class ModelCallError(HarnessError):
    code = "model_failed"


class HarnessTimeoutError(HarnessError):
    code = "timeout"


class PolicyDeniedError(HarnessError):
    code = "policy_denied"
