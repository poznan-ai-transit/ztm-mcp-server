# lifespans.py
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from fastmcp import FastMCP
from fastmcp.server.lifespan import lifespan

from logging_config import logger
from services.ztm_service import ZTMService
from services.ztm_realtime_delays import ZTMRealtimeDelays


@lifespan
async def ztm_service_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
    logger.info("Starting ZTM service lifespan")
    ztm_service: ZTMService = ZTMService.instance()
    # Keep MCP requests out until the initial collection has been validated and published.
    # Downloading/parsing runs off the event loop; failure prevents an empty server starting.
    await asyncio.to_thread(ztm_service.start_daily_refresh)

    try:
        realtime_service: ZTMRealtimeDelays = ZTMRealtimeDelays(ztm_service.repository)
        await asyncio.to_thread(realtime_service.start_auto_refresh, interval_seconds=30)
        yield {"ztm_static_storage": ztm_service.repository,
               "ztm_realtime_delays": realtime_service
               }
    finally:
        if realtime_service is not None:
            realtime_service.stop_auto_refresh()
        ztm_service.stop_daily_refresh()
        logger.info("ZTM service lifespan stopped")
