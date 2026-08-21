import logging

import pytest

from app.core.decorators import log_call, log_errors


def test_log_call_sync_logs_debug_and_returns_value(caplog):
    @log_call
    def add(a, b):
        return a + b

    with caplog.at_level(logging.DEBUG):
        result = add(1, 2)

    assert result == 3
    assert any(r.levelno == logging.DEBUG for r in caplog.records)


def test_log_call_sync_reraises_and_logs_error(caplog):
    @log_call
    def boom():
        raise ValueError("nope")

    with caplog.at_level(logging.DEBUG), pytest.raises(ValueError):
        boom()

    assert any(r.levelno == logging.ERROR for r in caplog.records)


async def test_log_call_async_logs_and_returns_value(caplog):
    @log_call
    async def add(a, b):
        return a + b

    with caplog.at_level(logging.DEBUG):
        result = await add(1, 2)

    assert result == 3
    assert any(r.levelno == logging.DEBUG for r in caplog.records)


def test_log_call_records_carry_event_and_target(caplog):
    @log_call
    def greet():
        return "hi"

    with caplog.at_level(logging.DEBUG):
        greet()

    record = next(r for r in caplog.records if r.levelno == logging.DEBUG)
    assert record.event == "method_call"
    assert record.target.endswith("greet")


def test_log_errors_wraps_public_methods_only(caplog):
    @log_errors
    class Dummy:
        def __init__(self):
            self.value = 1

        def public_method(self):
            return self.value

        def _private_method(self):
            return self.value

    dummy = Dummy()
    with caplog.at_level(logging.DEBUG):
        dummy.public_method()
        dummy._private_method()

    debug_targets = [r.target for r in caplog.records if r.levelno == logging.DEBUG]
    assert any("public_method" in t for t in debug_targets)
    assert not any("_private_method" in t for t in debug_targets)
