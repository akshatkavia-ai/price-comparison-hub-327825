import logging
import os
import time
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

from src.api.cache import CacheConfig, InMemoryTTLCache
from src.api.jobs import InMemoryJobManager
from src.api.logging_utils import get_request_id, init_logging, set_request_id
from src.api.models import (
    ComparePricesRequest,
    ComparePricesResponse,
    CreateJobRequest,
    CreateJobResponse,
    JobStatusResponse,
    PriceHistoryResponse,
)
from src.api.rate_limiter import InMemoryRateLimiter, RateLimitConfig
from src.api.service import InMemoryHistoryStore, PriceComparisonService

openapi_tags = [
    {"name": "Health", "description": "Service health and diagnostics."},
    {"name": "Prices", "description": "Price comparison and history APIs."},
    {"name": "Jobs", "description": "Background job management APIs."},
]

app = FastAPI(
    title="Price-Pal Edge Backend (FastAPI scaffold)",
    description=(
        "Implements Price-Pal backend endpoints for price comparison, background jobs, caching, "
        "rate limiting, and structured logging. This container currently runs a FastAPI scaffold "
        "while the intended production target is Supabase Edge Functions (Deno). The internal "
        "modules are designed to be portable so business logic can be migrated to Deno later."
    ),
    version="0.3.0",
    openapi_tags=openapi_tags,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- init logging ----
init_logging(os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("price-pal")

# ---- dependencies / singletons ----
_cache = InMemoryTTLCache()
_cache_config = CacheConfig(ttl_seconds=int(os.getenv("CACHE_TTL_SECONDS", "300")))
_history = InMemoryHistoryStore()
_svc = PriceComparisonService(cache=_cache, cache_config=_cache_config, history=_history)
_jobs = InMemoryJobManager(svc=_svc)
_rl = InMemoryRateLimiter()
_rl_config = RateLimitConfig(
    requests=int(os.getenv("RATE_LIMIT_REQUESTS", "30")),
    window_seconds=int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60")),
)


def _client_ip(request: Request) -> str:
    # Prefer proxy headers if present (common in deployments); fallback to socket.
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


async def rate_limit_dep(request: Request) -> None:
    ip = _client_ip(request)
    scope = f"{request.method}:{request.url.path}"
    allowed = _rl.allow(key=ip, scope=scope, config=_rl_config)
    if not allowed:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")


@app.middleware("http")
async def request_context_and_logging(request: Request, call_next):
    start = time.time()

    # Correlation id: accept from caller or generate
    incoming_rid = request.headers.get("x-request-id")
    set_request_id(incoming_rid or get_request_id())

    try:
        response = await call_next(request)
    except Exception:
        duration_ms = int((time.time() - start) * 1000)
        logger.exception(
            "Unhandled error",
            extra={
                "event": "request_failed",
                "method": request.method,
                "path": request.url.path,
                "duration_ms": duration_ms,
                "client_ip": _client_ip(request),
            },
        )
        return JSONResponse(status_code=500, content={"detail": "Internal server error", "request_id": get_request_id()})

    duration_ms = int((time.time() - start) * 1000)
    logger.info(
        "Request handled",
        extra={
            "event": "request_handled",
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
            "client_ip": _client_ip(request),
        },
    )
    response.headers["x-request-id"] = get_request_id()
    return response


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    logger.warning(
        "HTTP error",
        extra={
            "event": "http_error",
            "method": request.method,
            "path": request.url.path,
            "status_code": exc.status_code,
            "client_ip": _client_ip(request),
        },
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "request_id": get_request_id()},
        headers={"x-request-id": get_request_id()},
    )


@app.get("/", tags=["Health"], summary="Health check", operation_id="health_check__get")
# PUBLIC_INTERFACE
def health_check():
    """Health check endpoint.

    Returns:
        JSON object indicating the service is up.
    """
    return {"message": "Healthy"}


@app.get(
    "/docs/edge-usage",
    tags=["Health"],
    summary="Supabase Edge migration note",
    operation_id="edge_usage__get",
)
# PUBLIC_INTERFACE
def edge_usage_help():
    """Notes on reconciling this FastAPI scaffold with Supabase Edge Functions (Deno).

    This repository currently hosts a FastAPI backend to unblock end-to-end integration and API contracts.
    Intended production deployment is Supabase Edge Functions (Deno). Migration approach:
    - Keep API schemas stable (request/response models in `src/api/models.py`)
    - Port business logic from `service.py` and `scraper.py` to Deno (fetch + Cheerio/Playwright)
    - Replace `InMemoryTTLCache`, `InMemoryHistoryStore`, and `InMemoryJobManager` with Supabase Postgres/KV/Queues

    Returns:
        A short JSON message with the recommended migration path.
    """
    return {
        "note": "This is a FastAPI scaffold; business logic is modular for later porting to Supabase Edge (Deno).",
        "modules": ["src/api/models.py", "src/api/service.py", "src/api/scraper.py"],
    }


@app.post(
    "/compare-prices",
    response_model=ComparePricesResponse,
    tags=["Prices"],
    summary="Compare prices across supported sites",
    operation_id="compare_prices__post",
)
# PUBLIC_INTERFACE
async def compare_prices(
    payload: ComparePricesRequest,
    request: Request,
    _: None = Depends(rate_limit_dep),
    x_request_id: Optional[str] = Header(default=None, alias="x-request-id"),
):
    """Compare prices across multiple e-commerce platforms.

    Args:
        payload: Query and options.
        request: FastAPI request context.
        x_request_id: Optional correlation id passed by client.

    Returns:
        ComparePricesResponse containing offers, best offer, and cache indication.
    """
    if x_request_id:
        set_request_id(x_request_id)

    logger.info(
        "Compare requested",
        extra={"event": "compare_requested", "method": request.method, "path": request.url.path},
    )
    resp = await _svc.compare(payload)
    return resp


@app.post(
    "/create-job",
    response_model=CreateJobResponse,
    tags=["Jobs"],
    summary="Create a background scraping job",
    operation_id="create_job__post",
)
# PUBLIC_INTERFACE
async def create_job(
    payload: CreateJobRequest,
    request: Request,
    _: None = Depends(rate_limit_dep),
):
    """Create a background job to perform scraping and comparison asynchronously.

    Args:
        payload: Job creation payload.
        request: FastAPI request context.

    Returns:
        CreateJobResponse including job_id.
    """
    job_id = await _jobs.create_job(query=payload.query)
    logger.info(
        "Job created",
        extra={"event": "job_created", "method": request.method, "path": request.url.path, "job_id": job_id},
    )
    return CreateJobResponse(job_id=job_id, status="queued")


@app.get(
    "/job-status",
    response_model=JobStatusResponse,
    tags=["Jobs"],
    summary="Get job status",
    operation_id="job_status__get",
)
# PUBLIC_INTERFACE
async def job_status(
    request: Request,
    job_id: str = Query(..., description="Job id returned from /create-job"),
    _: None = Depends(rate_limit_dep),
):
    """Get the status of a background scraping job.

    Args:
        request: FastAPI request context.
        job_id: Job identifier.

    Returns:
        JobStatusResponse with progress and optional result.
    """
    st = await _jobs.get_status(job_id)
    if not st:
        raise HTTPException(status_code=404, detail="Job not found")
    return st


@app.get(
    "/price-history",
    response_model=PriceHistoryResponse,
    tags=["Prices"],
    summary="Get historical price snapshots for a query",
    operation_id="price_history__get",
)
# PUBLIC_INTERFACE
async def price_history(
    request: Request,
    query: str = Query(..., min_length=2, max_length=200, description="Query to fetch history for"),
    limit: int = Query(200, ge=1, le=2000, description="Max historical items to return"),
    _: None = Depends(rate_limit_dep),
):
    """Return historical price snapshots for the given query.

    Args:
        request: FastAPI request context.
        query: Query string.
        limit: Maximum items to return.

    Returns:
        PriceHistoryResponse containing historical items.
    """
    items = _svc.history(query=query, limit=limit)
    return PriceHistoryResponse(query=query, items=items)
