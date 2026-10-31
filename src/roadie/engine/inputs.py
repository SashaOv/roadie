"""Inputs a step fingerprints without building them."""

import hashlib
import json
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import cast

_run_state: ContextVar[dict[object, object] | None] = ContextVar(
    "roadie_run_state", default=None
)
_run_state_lock = threading.Lock()


def digest(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


# (spec) Input fingerprints
class Input(ABC):
    @abstractmethod
    def fingerprint(self, base_dir: Path) -> str:
        """Hash of this input as seen from a step's base directory."""


# (spec) Input fingerprints
@contextmanager
def run_scope() -> Iterator[None]:
    """Share `run_state` within the block, and release it when the block ends."""
    token = _run_state.set({})
    try:
        yield
    finally:
        _run_state.reset(token)


def run_state[S](key: object, factory: Callable[[], S]) -> S:
    """Return the state an input keeps under `key` for the current run.

    Within a run, every call with `key` returns the object `factory` made on
    the first call; the run releases it when it ends. Outside a run, every call
    returns a new object.
    """
    state = _run_state.get()
    if state is None:
        return factory()
    with _run_state_lock:
        if key not in state:
            state[key] = factory()
        return cast(S, state[key])
