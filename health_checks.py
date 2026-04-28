"""
Service-specific dependency probes for the /health/ready endpoint.

Each probe is an async callable returning (ok: bool, latency_ms: float).
Probes that aren't shared across services live here, not in the shared
`ai-trading-common` package.
"""

from __future__ import annotations

import asyncio
import time

from database import check_db_connection


async def check_postgresql() -> tuple[bool, float]:
    """Probe Postgres via the existing `check_db_connection` helper.

    `check_db_connection` is sync; wrap in `asyncio.to_thread` so we
    don't block the event loop while the probe runs.
    """
    start = time.perf_counter()
    ok = await asyncio.to_thread(check_db_connection)
    latency_ms = (time.perf_counter() - start) * 1000
    return (bool(ok), latency_ms)
