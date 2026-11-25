import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from fastmcp import Client, FastMCP

from cli import StartupOptions
from ini import Ini
from server import create_server, run_server
from services.ztm_realtime_delays import ZTMRealtimeDelays
from services.ztm_service import ZTMService


def test_create_server_returns_fresh_instance_without_starting_service(monkeypatch):
    start = Mock()
    monkeypatch.setattr(ZTMService, "start_daily_refresh", start)

    server: FastMCP[Any] = create_server()

    assert isinstance(server, FastMCP)
    assert create_server() is not server
    start.assert_not_called()


@pytest.mark.parametrize(
    "options",
    [
        StartupOptions(),
        StartupOptions(schedule_url=""),
        StartupOptions(schedule_url="https://example.test/feed"),
        StartupOptions(schedule_file=Path("local feed.zip")),
    ],
)
def test_server_starts_and_stops_service(monkeypatch, options):
    initialize = Mock(wraps=ZTMService.initialize)
    instance = Mock(wraps=ZTMService.instance)
    start = Mock()
    stop = Mock()
    monkeypatch.setattr(ZTMService, "start_daily_refresh", start)
    monkeypatch.setattr(ZTMService, "stop_daily_refresh", stop)
    monkeypatch.setattr(ZTMService, "initialize", initialize)
    monkeypatch.setattr(ZTMService, "instance", instance)
    monkeypatch.setattr(ZTMRealtimeDelays, "start_auto_refresh", Mock())
    monkeypatch.setattr(ZTMRealtimeDelays, "stop_auto_refresh", Mock())

    async def connect():
        async with Client(create_server(options)) as client:
            assert client.is_connected()
            initialize.assert_called_once_with(
                schedule_url=options.schedule_url,
                schedule_file=options.schedule_file,
            )
            instance.assert_called_once_with()
            start.assert_called_once_with()
            stop.assert_not_called()

    asyncio.run(connect())

    stop.assert_called_once_with()


def test_run_server_passes_http_settings(monkeypatch):
    monkeypatch.setattr("server.ini", Ini(server_host="127.0.0.1", server_port=9000))
    server: FastMCP[Any] = create_server()
    run = Mock()
    monkeypatch.setattr(server, "run", run)

    run_server(server)

    run.assert_called_once()
    assert run.call_args.kwargs["transport"] == "http"
    assert run.call_args.kwargs["host"] == "127.0.0.1"
    assert run.call_args.kwargs["port"] == 9000
    assert run.call_args.kwargs["stateless_http"] is True
