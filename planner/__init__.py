"""Script-to-Storyboard Visual Planner.

First-phase implementation: a deterministic skeleton that reads a short
drama script and produces the 8 core JSON artifacts described in
``specs/DATA_CONTRACTS.md``.

No LLM calls, no browser automation, no paid jobs. Downstream executors
(Flowith, libTV, ...) are intentionally out of scope.
"""

__version__ = "0.1.0"