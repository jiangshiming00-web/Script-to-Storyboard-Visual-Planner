"""LLM provider abstraction layer.

The planner pipeline delegates its non-visual intelligence steps —
bible building, beat extraction, shot generation, prompt compilation —
to a provider. The :class:`base.BaseProvider` interface lets us swap
``deterministic`` for a future OpenAI / Anthropic / local model adapter
without changing :mod:`pipeline` or the on-disk schemas.

v1.0 ships four registry entries:

* ``deterministic`` — the default. Reuses the existing
  :mod:`bible`, :mod:`beats`, :mod:`shots`, :mod:`prompts` modules
  as-is. **No real LLM is called in this phase.**
* ``openai`` — :mod:`openai_adapter`. Skeleton only: ``health_check``
  inspects ``PLANNER_OPENAI_API_KEY`` / ``OPENAI_API_KEY`` and the
  optional ``openai`` SDK presence. The five planning methods raise
  :class:`NotImplementedError` so Phase-1 never makes a real call.
* ``anthropic`` — :mod:`anthropic_adapter`. Mirror of the OpenAI
  adapter for Anthropic's key namespace and optional SDK.
* ``openai_compatible`` — :mod:`openai_compatible_adapter`. v1.0 ships
  a *runtime* implementation that drives any OpenAI Chat-Completions
  endpoint (OpenAI itself, vLLM, Ollama compat, internal gateways).
  ``health_check`` reports ``healthy=True`` only when
  ``enable_real_model_calls=True`` is configured AND the API key env
  var is non-empty AND ``base_url`` parses as ``http(s)://``.

Real model adapters must add their own SDK as **optional** dependencies
and must never run in production without the existing human-approval
and tool-agnostic guardrails (see :mod:`planner.env`).

Probe
-----

Phase 3 P2 probe design (per ``docs/design/provider_probe_design.md``)
adds an opt-in network reachability check (``probe()``) to each
adapter and a top-level CLI subcommand (``planner provider-probe``).
``probe()`` is distinct from ``health_check()`` — see brief §2.7 for
the 8-dimension separation table. Providers that don't expose a
remote endpoint (``deterministic``, ``openai`` / ``anthropic``
skeletons) keep the default raise; only ``openai_compatible`` ships a
real implementation that performs one HTTPS GET against the
configured ``base_url``.
"""

from .anthropic_adapter import AnthropicProvider
from .base import BaseProvider, ProviderHealth, ProviderProbeResult
from .deterministic import DeterministicProvider
from .openai_adapter import OpenAIProvider
from .openai_compatible_adapter import (
    IMPLEMENTED_TRUE,
    REAL_CALLS_DISABLED,
    REAL_CALLS_ENABLED,
    OpenAICompatibleProvider,
    http_get,
    http_post,
)
from .registry import available_providers, get_provider, register, unregister

# Importing each provider module runs ``@register`` so the registry is
# fully populated as soon as ``planner.providers`` is imported (which
# happens automatically when :mod:`planner.pipeline` runs or when
# :func:`planner.env._validate_provider` is called).
__all__ = [
    "BaseProvider",
    "ProviderHealth",
    "ProviderProbeResult",
    "DeterministicProvider",
    "OpenAIProvider",
    "AnthropicProvider",
    "OpenAICompatibleProvider",
    "IMPLEMENTED_TRUE",
    "REAL_CALLS_DISABLED",
    "REAL_CALLS_ENABLED",
    "http_get",
    "http_post",
    "available_providers",
    "get_provider",
    "register",
    "unregister",
]
