# lifespans.py
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from fastmcp import FastMCP
from fastmcp.server.lifespan import lifespan

from services.ztm_static_schedule import ZTMStaticSchedule
from services.ztm_service import ZTMService


@lifespan
async def ztm_service_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
    print("Starting ZTMService lifespan...")
    ztm_service: ZTMService = ZTMService.instance()
    ztm_service.start_daily_refresh()

    yield {"ztm_static_storage": ZTMStaticSchedule.instance()}

    ztm_service.stop_daily_refresh()