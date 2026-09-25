"""Tests for server creation, lifecycle, and HTTP startup configuration."""

import asyncio
from unittest.mock import Mock

from fastmcp import Client, FastMCP

from server import create_server, run_server
from services.ztm_service import ZTMService


def test_create_server_returns_fresh_instance_without_starting_service(monkeypatch):
    start = Mock()
    monkeypatch.setattr(ZTMService, "start_daily_refresh", start)

    server = create_server()

    assert isinstance(server, FastMCP)
    assert create_server() is not server
    start.assert_not_called()


def test_server_starts_and_stops_service(monkeypatch):
    start = Mock()
    stop = Mock()
    monkeypatch.setattr(ZTMService, "start_daily_refresh", start)
    monkeypatch.setattr(ZTMService, "stop_daily_refresh", stop)

    async def connect():
        async with Client(create_server()) as client:
            assert client.is_connected()
            start.assert_called_once_with()
            stop.assert_not_called()

    asyncio.run(connect())

    stop.assert_called_once_with()


def test_run_server_passes_http_settings(monkeypatch):
    server = create_server()
    run = Mock()
    monkeypatch.setattr(server, "run", run)

    run_server(server)

    run.assert_called_once()
    assert run.call_args.kwargs["transport"] == "http"
    assert run.call_args.kwargs["host"] == "0.0.0.0"
    assert run.call_args.kwargs["port"] == 8000
    assert run.call_args.kwargs["stateless_http"] is True
