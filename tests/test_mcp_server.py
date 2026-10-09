"""Tests for the MCP tool and resource callables.

FastMCP's ``@mcp.tool()`` / ``@mcp.resource()`` register the function as a side
effect but return the original callable, so they can be invoked directly.
"""

from __future__ import annotations

from types import SimpleNamespace
import asyncio

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from mcp_server.resources import list_routes_and_stops
from mcp_server.tools import (
    echo, search_routes, search_stops, get_departures, get_routes_for_stop, mcp_tools,
)
from services.ztm_static_schedule import ZTMStaticSchedule

# --------------------------------------------------------------------------- #
# echo tool
# --------------------------------------------------------------------------- #


def test_echo_returns_input():
    assert echo("hello") == "hello"


def test_echo_preserves_polish_diacritics():
    assert echo("Świętego Marcina") == "Świętego Marcina"


def test_echo_empty_string():
    assert echo("") == ""


def test_search_stops_uses_schedule_wrapper(sample_gtfs):
    ZTMStaticSchedule.load(sample_gtfs)
    results = search_stops("zabink")
    assert results
    assert results[0]["stop_id"] == "S2"
    assert results[0]["stop_name"] == "Żabinko"


def test_search_routes_uses_schedule_wrapper(sample_gtfs):
    ZTMStaticSchedule.load(sample_gtfs)
    results = search_routes("Łęczyca")
    assert results
    assert results[0]["route_id"] == "R1"
    assert results[0]["route_long_name"] == "Łęczyca"
    assert "directions" in results[0]


def test_departures_filter_line_before_limit(sample_gtfs):
    ZTMStaticSchedule.load(sample_gtfs)
    result = get_departures("S2", "R2", "2026-06-15", "05:00", limit=1)
    assert result["stop_name"] == "Żabinko"
    assert result["route_short_name"] == "2"
    assert result["data_source"] == "static_mock"
    assert result["realtime"] is False
    assert [d["departure_time"] for d in result["departures"]] == ["06:10:00"]
    assert result["departures"][0]["trip_headsign"] == "Dworzec"


def test_departures_filter_direction_before_limit(sample_gtfs):
    sample_gtfs["trips"][1]["direction_id"] = "1"
    ZTMStaticSchedule.load(sample_gtfs)
    result = get_departures("S1", "R1", "2026-06-15", "05:00", direction_id=1, limit=1)
    assert [d["trip_id"] for d in result["departures"]] == ["T2"]
    assert result["departures"][0]["departure_datetime"] == "2026-06-16T01:30:00"


@pytest.mark.parametrize("stop,route,day,time,direction", [
    ("S3", "R1", "2026-06-15", "05:00", None),
    ("S2", "R2", "2026-06-15", "06:11", None),
    ("S2", "R2", "2026-06-14", "05:00", None),
    ("S2", "R2", "2026-07-01", "05:00", None),
    ("S2", "R2", "2026-06-15", "05:00", 0),
])
def test_departures_no_matching_service(sample_gtfs, stop, route, day, time, direction):
    ZTMStaticSchedule.load(sample_gtfs)
    assert get_departures(stop, route, day, time, direction)["departures"] == []


def test_departures_calendar_exceptions_and_inclusive_time(sample_gtfs):
    sample_gtfs["calendar_dates"] = [
        {"service_id": "WKD", "date": "20260614", "exception_type": "1"},
        {"service_id": "WKD", "date": "20260615", "exception_type": "2"},
    ]
    ZTMStaticSchedule.load(sample_gtfs)
    result = get_departures("S2", "R2", "2026-06-14", "06:10")
    assert result == get_departures("S2", "R2", "2026-06-14", "06:10")
    assert result["departures"][0]["departure_time"] == "06:10:00"
    assert get_departures("S2", "R2", "2026-06-15", "06:10")["departures"] == []


@pytest.mark.parametrize("override", [
    {"date": "2026-02-30"}, {"date": "20260615"},
    {"time": "24:00"}, {"time": "06:60"}, {"time": "6:10"},
    {"limit": 0}, {"limit": -1}, {"limit": 101}, {"direction_id": 2},
    {"stop_id": "missing"}, {"route_id": "missing"},
])
def test_departures_invalid_input(sample_gtfs, override):
    ZTMStaticSchedule.load(sample_gtfs)
    arguments = dict(stop_id="S2", route_id="R2", date="2026-06-15", time="06:10")
    with pytest.raises(ToolError):
        get_departures(**(arguments | override))


def test_routes_for_stop_are_sorted_and_serializable(sample_gtfs):
    ZTMStaticSchedule.load(sample_gtfs)
    result = get_routes_for_stop("S2")
    assert [r["route_id"] for r in result] == ["R1", "R2"]
    assert result[0]["route_short_name"] == "1"
    assert result == get_routes_for_stop("S2")
    with pytest.raises(ToolError, match="search_stops"):
        get_routes_for_stop("missing")


def test_new_tools_through_mcp(sample_gtfs):
    ZTMStaticSchedule.load(sample_gtfs)

    async def check():
        async with Client(mcp_tools) as client:
            names = {tool.name for tool in await client.list_tools()}
            assert {"get_departures", "get_routes_for_stop"} <= names
            departures = await client.call_tool("get_departures", {
                "stop_id": "S2", "route_id": "R2", "date": "2026-06-15", "time": "06:10",
            })
            assert departures.data["departures"][0]["departure_time"] == "06:10:00"
            routes = await client.call_tool("get_routes_for_stop", {"stop_id": "S2"})
            assert [r["route_id"] for r in routes.data] == ["R1", "R2"]

    asyncio.run(check())


# --------------------------------------------------------------------------- #
# list_routes_and_stops resource
# --------------------------------------------------------------------------- #


def _ctx_with(schedule: ZTMStaticSchedule) -> SimpleNamespace:
    """Minimal stand-in for a fastmcp Context exposing the lifespan storage."""
    return SimpleNamespace(
        request_context=SimpleNamespace(lifespan_context={"ztm_static_storage": schedule})
    )


def test_list_routes_and_stops_empty_storage():
    out = list_routes_and_stops(_ctx_with(ZTMStaticSchedule.instance()))
    assert out == {"routes": [], "stops": []}


def test_list_routes_and_stops_reflects_storage(sample_gtfs):
    schedule = ZTMStaticSchedule.load(sample_gtfs)
    out = list_routes_and_stops(_ctx_with(schedule))
    assert {r["route_id"] for r in out["routes"]} == {"R1", "R2"}
    assert {s["stop_id"] for s in out["stops"]} == {"S1", "S2", "S3"}


def test_list_routes_and_stops_returns_plain_json_types(sample_gtfs):
    schedule = ZTMStaticSchedule.load(sample_gtfs)
    out = list_routes_and_stops(_ctx_with(schedule))
    assert type(out["routes"]) is list
    assert type(out["routes"][0]) is dict
    assert type(out["stops"]) is list
    assert type(out["stops"][0]) is dict


# --------------------------------------------------------------------------- #
# Integration: real service output flowing through storage into the resource
# --------------------------------------------------------------------------- #


@pytest.mark.slow
def test_service_to_storage_to_resource_pipeline(real_gtfs):
    """Mirror the production path: ZTMService loads -> storage holds -> resource reads."""
    schedule = ZTMStaticSchedule.load(real_gtfs)

    out = list_routes_and_stops(_ctx_with(schedule))
    assert len(out["routes"]) == len(real_gtfs["routes"])
    assert len(out["stops"]) == len(real_gtfs["stops"])
    # Diacritics survive the full round trip.
    names = "".join(s.get("stop_name", "") for s in out["stops"])
    assert any(ch in names for ch in "ąćęłńóśźżĄĆĘŁŃÓŚŹŻ")
