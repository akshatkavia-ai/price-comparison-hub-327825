from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional

from src.api.models import ComparePricesRequest, ComparePricesResponse, JobStatusResponse
from src.api.service import PriceComparisonService


@dataclass
class _JobState:
    job_id: str
    status: str
    progress: int
    message: Optional[str]
    result: Optional[ComparePricesResponse]
    updated_at: datetime


class InMemoryJobManager:
    """Minimal in-memory background job processing."""

    def __init__(self, svc: PriceComparisonService) -> None:
        self._svc = svc
        self._jobs: Dict[str, _JobState] = {}
        self._lock = asyncio.Lock()

    async def create_job(self, query: str) -> str:
        job_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        async with self._lock:
            self._jobs[job_id] = _JobState(
                job_id=job_id,
                status="queued",
                progress=0,
                message=None,
                result=None,
                updated_at=now,
            )

        # Fire-and-forget background task
        asyncio.create_task(self._run(job_id=job_id, query=query))
        return job_id

    async def _update(self, job_id: str, **kwargs) -> None:
        async with self._lock:
            st = self._jobs.get(job_id)
            if not st:
                return
            for k, v in kwargs.items():
                setattr(st, k, v)
            st.updated_at = datetime.now(timezone.utc)

    async def _run(self, job_id: str, query: str) -> None:
        await self._update(job_id, status="running", progress=10, message="Starting scrape")
        try:
            result = await self._svc.compare(
                ComparePricesRequest(query=query, use_cache=False, max_results_per_site=5)
            )
            await self._update(job_id, status="succeeded", progress=100, message="Done", result=result)
        except Exception as e:
            await self._update(job_id, status="failed", progress=100, message=str(e), result=None)

    async def get_status(self, job_id: str) -> Optional[JobStatusResponse]:
        async with self._lock:
            st = self._jobs.get(job_id)
            if not st:
                return None
            return JobStatusResponse(
                job_id=st.job_id,
                status=st.status,  # type: ignore[arg-type]
                progress=st.progress,
                message=st.message,
                result=st.result,
                updated_at=st.updated_at,
            )
