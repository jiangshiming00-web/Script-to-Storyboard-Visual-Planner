"""Provider registry.

Providers register themselves via the :func:`register` decorator. The
default implementation does no filesystem or network I/O — lookups are
purely in-memory — so swapping a provider is just a configuration
change. Unknown providers raise :class:`ConfigError` so the failure is
diagnosed immediately, not later when the pipeline tries to call the
missing method.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Type

from ..exceptions import ConfigError
from .base import BaseProvider

if TYPE_CHECKING:
    from ..model_config import ProviderRuntimeSettings

_REGISTRY: Dict[str, Type[BaseProvider]] = {}


def register(name: str) -> callable:
    """Decorator that registers a provider class under ``name``."""

    if not isinstance(name, str) or not name:
        raise ValueError("Provider name must be a non-empty string")

    def _decorator(cls: Type[BaseProvider]) -> Type[BaseProvider]:
        if not issubclass(cls, BaseProvider):
            raise TypeError(
                f"Provider {name!r} must subclass BaseProvider, "
                f"got {cls!r}."
            )
        # Last writer wins; if a real conflict arises we want the failure
        # here rather than silent shadowing.
        if name in _REGISTRY and _REGISTRY[name] is not cls:
            raise RuntimeError(
                f"Provider {name!r} already registered as "
                f"{_REGISTRY[name].__name__}; refusing to overwrite with "
                f"{cls.__name__}."
            )
        cls.name = name
        _REGISTRY[name] = cls
        return cls

    return _decorator


def get_provider(
    name: str,
    settings: "ProviderRuntimeSettings | None" = None,
) -> BaseProvider:
    """Instantiate the provider registered under ``name``.

    Args:
        name: Registered provider name (e.g. ``"deterministic"``,
            ``"openai_compatible"``).
        settings: Optional :class:`ProviderRuntimeSettings` for
            real-model providers. Deterministic / skeleton providers
            accept and ignore this. Passing it to a provider that
            does not consume it is harmless.

    Raises:
        ConfigError: if ``name`` is not registered. The error message
            lists available names so users can fix the config without
            reading the source.
    """

    if not isinstance(name, str) or not name:
        raise ConfigError(
            f"planner_provider must be a non-empty string, got {name!r}."
        )
    cls = _REGISTRY.get(name)
    if cls is None:
        avail = ", ".join(sorted(_REGISTRY)) or "(none registered)"
        raise ConfigError(
            f"Unknown planner_provider: {name!r}. Available: {avail}."
        )
    return cls(settings)


def available_providers() -> List[str]:
    """Return the sorted list of registered provider names."""

    return sorted(_REGISTRY.keys())


def unregister(name: str) -> None:
    """Remove a provider from the registry.

    Intended for test teardown so a stub provider does not leak into
    other tests. Calling this with an unknown name is a silent no-op
    so test cleanup is idempotent.

    Raises:
        ValueError: if ``name`` is empty.
    """

    if not isinstance(name, str) or not name:
        raise ValueError("Provider name must be a non-empty string")
    _REGISTRY.pop(name, None)
