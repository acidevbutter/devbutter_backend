import logging

from fastapi.responses import JSONResponse

from app import main


class _ReadyConnection:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    async def execute(self, _query) -> None:
        return None


class _ReadyEngine:
    def connect(self) -> _ReadyConnection:
        return _ReadyConnection()


class _UnavailableEngine:
    def connect(self) -> _ReadyConnection:
        raise ConnectionError("database unavailable")


async def test_readiness_checks_database_connection(monkeypatch, caplog) -> None:
    monkeypatch.setattr(main, "engine", _ReadyEngine())

    with caplog.at_level(logging.WARNING):
        response = await main.readiness()

    assert response == {"status": "ready"}
    assert not [r for r in caplog.records if getattr(r, "event", None) == "health_check_failed"]


async def test_readiness_logs_warning_when_database_is_unavailable(monkeypatch, caplog) -> None:
    monkeypatch.setattr(main, "engine", _UnavailableEngine())

    with caplog.at_level(logging.WARNING):
        response = await main.readiness()

    assert isinstance(response, JSONResponse)
    assert response.status_code == 503
    assert response.body == b'{"status":"unavailable"}'
    assert [r for r in caplog.records if getattr(r, "event", None) == "health_check_failed"]
