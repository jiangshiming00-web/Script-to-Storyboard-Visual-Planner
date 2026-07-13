"""Error mapping for the FastAPI layer.

The web layer must never leak a Python traceback to the HTTP client —
that would violate the project's "rejected loudly, never silently"
error-handling discipline (see ``CLAUDE.md``). This module maps each
:class:`planner.exceptions.PlannerError` subclass to a stable HTTP
status code and a JSON body with two fields only: ``error`` (machine
type) and ``message`` (human text).

The full traceback is logged server-side via ``logging.exception`` so
operators can still diagnose failures; it just never crosses the HTTP
boundary.

Mapping rules:

- :class:`ConfigError` → 400 Bad Request (operator error in config)
- :class:`ScriptReadError` → 404 Not Found (script file missing)
- :class:`EnvironmentBoundaryError` → 403 Forbidden (boundary violation)
- :class:`ProviderUnavailableError` → 503 Service Unavailable
  (provider's ``health_check`` failed — pipeline refused to start it)
- :class:`ProviderOutputError` → 502 Bad Gateway (provider's HTTP
  response was malformed / schema mismatch — upstream rejected the
  request). Distinct from 503 because the cause is the upstream
  endpoint's payload, not provider reachability.
- :class:`BrokenReferenceError` → 500 Internal Server Error
  (bug in pipeline output; user-visible via /validate)
- :class:`SchemaValidationError` → 500 Internal Server Error
- :class:`PlannerError` (base) → 500 Internal Server Error
- Anything else (programming bug) → 500 with type "UnhandledError"
  and **no traceback in the body**; full traceback in server logs.
"""

from __future__ import annotations

import logging
from typing import Tuple

from ..exceptions import (
    BrokenReferenceError,
    ConfigError,
    EnvironmentBoundaryError,
    PlannerError,
    ProviderOutputError,
    ProviderUnavailableError,
    SchemaValidationError,
    ScriptReadError,
)

_log = logging.getLogger(__name__)


_STATUS_MAP = {
    ConfigError: (400, "ConfigError"),
    ScriptReadError: (404, "ScriptReadError"),
    EnvironmentBoundaryError: (403, "EnvironmentBoundaryError"),
    ProviderUnavailableError: (503, "ProviderUnavailableError"),
    ProviderOutputError: (502, "ProviderOutputError"),
    BrokenReferenceError: (500, "BrokenReferenceError"),
    SchemaValidationError: (500, "SchemaValidationError"),
}


def classify(exc: BaseException) -> Tuple[int, str, str]:
    """Return ``(status_code, error_type, message)`` for an exception.

    ``error_type`` is the class name (stable identifier). ``message`` is
    ``str(exc)`` — already user-friendly because ``PlannerError``
    subclasses raise with full sentences.
    """

    if isinstance(exc, PlannerError):
        status, err_type = _STATUS_MAP.get(
            type(exc), (500, type(exc).__name__)
        )
        return status, err_type, str(exc) or err_type

    # Non-PlannerError is a programming bug. Log full traceback server-side.
    _log.exception("Unhandled error in web layer")
    return 500, "UnhandledError", "An internal error occurred."


def is_planner_error(exc: BaseException) -> bool:
    """Return True if ``exc`` is any ``PlannerError`` subclass."""

    return isinstance(exc, PlannerError)