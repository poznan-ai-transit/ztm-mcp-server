"""Date selection and URI handling for routes/stops resources."""

import asyncio
import json
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastmcp import Client

from mcp_server.resources import list_routes_and_stops
from server import create_server
from services.ztm_schedule_repository import WARSAW, ScheduleEntry, ScheduleRepository
from services.ztm_service import ZTMService
from services.ztm_static_schedule import ZTMStaticSchedule


@pytest.fixture
def repository(sample_gtfs):
    june = ZTMStaticSchedule.load(sample_gtfs)
    july_gtfs = deepcopy(sample_gtfs)
    july_gtfs["feed_info"][0].update(feed_start_date="20260701", feed_end_date="20260731")
    july_gtfs["routes"][0]["route_long_name"] = "July route"
    july_gtfs["stops"][0]["stop_name"] = "July stop"
    july = ZTMStaticSchedule.load(july_gtfs)
    modified = datetime(2026, 6, 1, tzinfo=WARSAW)
    return ScheduleRepository(
        [ScheduleEntry("june.zip", modified, june), ScheduleEntry("july.zip", modified, july)]
    )


def _ctx(repository):
    return SimpleNamespace(
        request_context=SimpleNamespace(lifespan_context={"ztm_static_storage": repository})
    )


def test_resource_selects_feed_by_date(repository):
    june = list_routes_and_stops("2026-06-15", _ctx(repository))
    july = list_routes_and_stops("2026-07-15", _ctx(repository))

    assert june["routes"][0]["route_long_name"] != "July route"
    assert june["stops"][0]["stop_name"] != "July stop"
    assert july["routes"][0]["route_long_name"] == "July route"
    assert july["stops"][0]["stop_name"] == "July stop"


def test_resource_requires_date(repository):
    with pytest.raises(TypeError, match="date"):
        list_routes_and_stops(ctx=_ctx(repository))


@pytest.mark.parametrize("value", ["20260715", "2026-W29-3", "2026-02-30", "15-07-2026", ""])
def test_resource_rejects_invalid_date(repository, value):
    with pytest.raises(ValueError, match="Invalid date"):
        list_routes_and_stops(value, _ctx(repository))


def test_resource_rejects_date_outside_loaded_feeds(repository):
    with pytest.raises(ValueError, match="No GTFS schedule covers 2026-08-01"):
        list_routes_and_stops("2026-08-01", _ctx(repository))


def test_dated_resource_through_mcp(repository, monkeypatch):
    ZTMService.instance().repository = repository
    monkeypatch.setattr(ZTMService, "start_daily_refresh", lambda self: None)
    monkeypatch.setattr(ZTMService, "stop_daily_refresh", lambda self: None)

    async def check():
        async with Client(create_server()) as client:
            assert await client.list_resources() == []
            templates = await client.list_resource_templates()
            assert [template.uriTemplate for template in templates] == [
                "resource://ztm_static_storage/list_routes_and_stops/{date}"
            ]
            result = await client.read_resource(
                "resource://ztm_static_storage/list_routes_and_stops/2026-07-15"
            )
            data = json.loads(result[0].text)
            assert data["routes"][0]["route_long_name"] == "July route"
            assert data["stops"][0]["stop_name"] == "July stop"

    asyncio.run(check())
