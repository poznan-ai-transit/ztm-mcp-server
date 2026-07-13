"""Tests for the MCP tool and resource callables.

FastMCP's ``@mcp.tool()`` / ``@mcp.resource()`` register the function as a side
effect but return the original callable, so they can be invoked directly.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from mcp_server.resources import list_routes_and_stops
from mcp_server.tools import echo
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
    schedule = ZTMStaticSchedule.instance()
    schedule.set_static_gtfs(sample_gtfs)
    out = list_routes_and_stops(_ctx_with(schedule))
    assert {r["route_id"] for r in out["routes"]} == {"R1", "R2"}
    assert {s["stop_id"] for s in out["stops"]} == {"S1", "S2"}


def test_list_routes_and_stops_returns_plain_json_types(sample_gtfs):
    schedule = ZTMStaticSchedule.instance()
    schedule.set_static_gtfs(sample_gtfs)
    out = list_routes_and_stops(_ctx_with(schedule))
    assert type(out["routes"]) is list
    assert type(out["routes"][0]) is dict
    assert type(out["stops"][0]) is dict


# --------------------------------------------------------------------------- #
# Integration: real service output flowing through storage into the resource
# --------------------------------------------------------------------------- #


@pytest.mark.slow
def test_service_to_storage_to_resource_pipeline(real_gtfs):
    """Mirror the production path: ZTMService loads -> storage holds -> resource reads."""
    schedule = ZTMStaticSchedule.instance()
    schedule.set_static_gtfs(real_gtfs)

    out = list_routes_and_stops(_ctx_with(schedule))
    assert len(out["routes"]) == len(real_gtfs["routes"])
    assert len(out["stops"]) == len(real_gtfs["stops"])
    # Diacritics survive the full round trip.
    names = "".join(s.get("stop_name", "") for s in out["stops"])
    assert any(ch in names for ch in "ąćęłńóśźżĄĆĘŁŃÓŚŹŻ")
