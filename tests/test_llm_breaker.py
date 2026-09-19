"""Prove that repeated model failures stop calls and recover through one probe."""

from app.llm.breaker import CircuitBreaker, CircuitState


def test_two_consecutive_failures_open_and_a_later_probe_closes_the_breaker():
    now = [10.0]
    breaker = CircuitBreaker(recovery_seconds = 30, clock = lambda: now[0])

    breaker.record_failure()
    assert breaker.state == CircuitState.CLOSED
    breaker.record_failure()
    assert breaker.state == CircuitState.OPEN
    assert breaker.allow_call() is False

    now[0] = 39.9
    assert breaker.allow_call() is False
    now[0] = 40.0
    assert breaker.state == CircuitState.HALF_OPEN
    assert breaker.allow_call() is True

    breaker.record_success()
    assert breaker.state == CircuitState.CLOSED


def test_three_failures_in_ten_calls_open_even_when_not_consecutive():
    breaker = CircuitBreaker()
    for outcome in [False, True, False, True, False]:
        breaker.record_success() if outcome else breaker.record_failure()
    assert breaker.state == CircuitState.OPEN


def test_failed_half_open_probe_opens_the_breaker_again():
    now = [0.0]
    breaker = CircuitBreaker(recovery_seconds = 30, clock = lambda: now[0])
    breaker.force_open()
    now[0] = 30.0
    assert breaker.state == CircuitState.HALF_OPEN
    breaker.record_failure()
    assert breaker.state == CircuitState.OPEN
