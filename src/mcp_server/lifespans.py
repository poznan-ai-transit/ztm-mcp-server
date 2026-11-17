# lifespans.py
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from fastmcp import FastMCP
from fastmcp.server.lifespan import lifespan

from logging_config import logger
from services.ztm_service import ZTMService


@lifespan
async def ztm_service_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
    logger.info("Starting ZTM service lifespan")
    ztm_service: ZTMService = ZTMService.instance()
    # Keep MCP requests out until the initial collection has been validated and published.
    # Downloading/parsing runs off the event loop; failure prevents an empty server starting.
    logger.info("Loading initial GTFS schedules before accepting MCP requests")
    await asyncio.to_thread(ztm_service.refresh_schedules)
    ztm_service.start_daily_refresh(refresh_immediately=False)

    try:
        yield {"ztm_static_storage": ztm_service.repository}
    finally:
        ztm_service.stop_daily_refresh()
        logger.info("ZTM service lifespan stopped")
