"""Custom exceptions for the planner package."""


class PlannerError(Exception):
    """Base exception for all planner errors."""


class ConfigError(PlannerError):
    """Raised when environment configuration is missing or invalid."""


class ScriptReadError(PlannerError):
    """Raised when the input script cannot be read or parsed."""


class SchemaValidationError(PlannerError):
    """Raised when generated artifacts fail schema validation."""


class BrokenReferenceError(PlannerError):
    """Raised when a shot references an unknown character/location/prop id."""


class EnvironmentBoundaryError(PlannerError):
    """Raised when an action is forbidden by environment boundary rules."""


class ProviderUnavailableError(PlannerError):
    """Raised when the requested provider fails its health check.

    The pipeline calls ``provider.health_check()`` before invoking any
    extraction step. If the check fails AND the environment is
    ``fail-closed`` (i.e. ``production`` or development with
    ``allow_provider_fallback=False``), the pipeline raises this
    exception so the operator sees a loud failure instead of a silent
    fallback to a different provider.
    """


class ProviderOutputError(PlannerError):
    """Raised when a provider's HTTP response cannot be parsed into
    the expected Pydantic schema (malformed JSON, missing fields,
    wrong shape, etc.).

    Production must NEVER silently fall back to deterministic when
    this is raised — the v1.0 contract says the operator should see
    the structured error. JSON parse failures must surface as this
    exception, NOT be swallowed and routed to deterministic. The
    error message should include the provider / model / step and a
    truncated excerpt of the offending payload (never the API key).
    """


class ProviderProbeError(PlannerError):
    """Raised when a provider probe (operator-initiated, opt-in
    network reachability check) fails or is not implemented.

    Distinct from :class:`ProviderUnavailableError` (which gates
    pipeline selection at run time) and :class:`ProviderOutputError`
    (which gates response parsing). ``probe()`` failure modes:

    * adapter raises ``NotImplementedError`` (skeleton / no remote
      endpoint) — CLI top-level catches and wraps to
      ``ProviderProbeError(reason="not_implemented")`` → **exit 1**.
    * adapter reachable but unhealthy (HTTP 4xx/5xx / DNS / timeout)
      — adapter returns ``ProviderProbeResult(healthy=False)``;
      CLI exits **2** (no exception raised).
    * subcommand invoked without env gate set — CLI emits a
      one-line stderr policy refusal and exits **2** (``ctx.exit(2)``,
      NOT ``Click.UsageError`` which would print multi-line usage).

    Caught by the CLI ``provider-probe`` subcommand's
    ``try/except PlannerError`` so the user sees the structured
    message rather than a Python traceback. Never bundled with the
    pipeline's run-time selection logic — probe is off the
    hot path.
    """