# lifespans.py
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from fastmcp import FastMCP
from fastmcp.server.lifespan import lifespan

from logging_config import logger
from services.ztm_service import ZTMService
from services.ztm_static_schedule import ZTMStaticSchedule


@lifespan
async def ztm_service_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
    logger.info("Starting ZTM service lifespan")
    ztm_service: ZTMService = ZTMService.instance()
    ztm_service.start_daily_refresh()

    try:
        yield {"ztm_static_storage": ZTMStaticSchedule.instance()}
    finally:
        ztm_service.stop_daily_refresh()
        logger.info("ZTM service lifespan stopped")
