"""The speed-up of the tests must never leak into production: pin the real password hashing cost."""

from permita38.core.service import auth
from tests.backend.conftest import PRODUCTION_PBKDF2_ITERATIONS


def test_production_cost_factor_is_unchanged():
    assert PRODUCTION_PBKDF2_ITERATIONS >= 200_000


def test_hashes_are_verified_with_their_own_iteration_count(monkeypatch):
    monkeypatch.setattr(auth, "PBKDF2_ITERATIONS", 1000)
    cheap = auth.hash_password("correct horse battery")
    monkeypatch.setattr(auth, "PBKDF2_ITERATIONS", 5000)
    assert auth.verify_password("correct horse battery", cheap)
    assert not auth.verify_password("wrong", cheap)
    assert cheap.split("$")[1] == "1000"
