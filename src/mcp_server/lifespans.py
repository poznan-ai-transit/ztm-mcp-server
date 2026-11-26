# lifespans.py
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from fastmcp import FastMCP
from fastmcp.server.lifespan import lifespan

from logging_config import logger
from services.ztm_realtime_delays import ZTMRealtimeDelays
from services.ztm_service import ZTMService
from services.ztm_service_alerts import ZTMServiceAlerts


@lifespan
async def ztm_service_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
    logger.info("Starting ZTM service lifespan")
    ztm_service: ZTMService = ZTMService.instance()
    # Keep MCP requests out until the initial collection has been validated and published.
    # Downloading/parsing runs off the event loop; failure prevents an empty server starting.
    await asyncio.to_thread(ztm_service.start_daily_refresh)
    realtime_service: ZTMRealtimeDelays | None = None
    alerts_service: ZTMServiceAlerts | None = None

    try:
        realtime_service = ZTMRealtimeDelays(ztm_service.repository)
        await asyncio.to_thread(realtime_service.start_auto_refresh, interval_seconds=30)
        alerts_service = ZTMServiceAlerts()
        await asyncio.to_thread(alerts_service.start_auto_refresh, interval_seconds=60)
        yield {
            "ztm_static_storage": ztm_service.repository,
            "ztm_realtime_delays": realtime_service,
            "ztm_service_alerts": alerts_service,
        }
    finally:
        if alerts_service is not None:
            alerts_service.stop_auto_refresh()
        if realtime_service is not None:
            realtime_service.stop_auto_refresh()
        ztm_service.stop_daily_refresh()
        logger.info("ZTM service lifespan stopped")
