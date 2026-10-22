"""Shared setup for the randomized tests: hypothesis profiles, marker, fast password hashing."""

import os
import types

import pytest
from hypothesis import HealthCheck, settings

SUPPRESS = [HealthCheck.too_slow, HealthCheck.data_too_large, HealthCheck.filter_too_much]

# `ci` is deterministic and fast; `thorough` explores much more and is not deterministic.
settings.register_profile(
    "ci", max_examples=25, stateful_step_count=35, deadline=None, derandomize=True,
    print_blob=True, suppress_health_check=SUPPRESS,
)  # fmt: skip
settings.register_profile(
    "thorough", max_examples=300, stateful_step_count=80, deadline=None, derandomize=False,
    print_blob=True, suppress_health_check=SUPPRESS,
)  # fmt: skip
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "ci"))


def pytest_configure(config):
    config.addinivalue_line("markers", "randomized: model-based / fuzz tests driven by random input")
    seed = os.environ.get("PERMITA38_FUZZ_SEED")
    if seed and hasattr(config.option, "hypothesis_seed"):
        config.option.hypothesis_seed = int(seed)
        settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "thorough"))


def pytest_collection_modifyitems(items):
    here = os.path.dirname(__file__)
    for item in items:
        if str(item.fspath).startswith(here):
            item.add_marker(pytest.mark.randomized)


@pytest.fixture(autouse=True)
def fast_password_hashing(monkeypatch):
    """PBKDF2 at 200k iterations makes hundreds of random logins slow; the logic under test is the same."""
    from permita38.core.service import auth

    monkeypatch.setattr(auth, "PBKDF2_ITERATIONS", 1000)


class FakeClock:
    """Replaces the `time` module inside permita38.core.service.auth so token expiry, password-change
    invalidation and the login throttle can be driven deterministically."""

    def __init__(self, now: float):
        self.now = float(now)

    def time(self) -> float:
        return self.now

    def advance(self, seconds: float):
        self.now += seconds

    def as_module(self):
        return types.SimpleNamespace(time=self.time)
