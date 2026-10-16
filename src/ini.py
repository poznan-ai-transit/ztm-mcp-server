"""Shared application configuration, created once for the process lifetime."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Ini:
    server_host: str = "0.0.0.0"
    server_port: int = 8000
    gtfs_endpoint: str = "https://www.ztm.poznan.pl/pl/dla-deweloperow/getGTFSFile"
    request_timeout_seconds: float = 30
    refresh_retry_initial_seconds: int = 60
    refresh_retry_max_seconds: int = 1800


ini = Ini()
