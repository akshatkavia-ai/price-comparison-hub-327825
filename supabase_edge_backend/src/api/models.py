from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, HttpUrl


class ComparePricesRequest(BaseModel):
    query: str = Field(..., min_length=2, max_length=200, description="Product name or search query.")
    use_cache: bool = Field(True, description="Whether to use cached results if available and fresh.")
    max_results_per_site: int = Field(
        5, ge=1, le=20, description="Maximum number of results to return per site."
    )


class PriceOffer(BaseModel):
    site: Literal["gamestheshop", "gamenation", "gameloot", "amazon", "flipkart"] = Field(
        ..., description="E-commerce source identifier."
    )
    title: str = Field(..., description="Offer title as displayed on the site.")
    price_inr: Optional[float] = Field(None, description="Parsed price in INR, if available.")
    currency: str = Field("INR", description="Currency code (currently INR).")
    url: HttpUrl = Field(..., description="Direct product/offer URL.")
    image_url: Optional[HttpUrl] = Field(None, description="Offer image URL if found.")
    in_stock: Optional[bool] = Field(None, description="Stock status if it can be inferred.")
    raw: Dict[str, Any] = Field(default_factory=dict, description="Raw extracted fields for debugging.")


class ComparePricesResponse(BaseModel):
    query: str = Field(..., description="Original query.")
    offers: List[PriceOffer] = Field(..., description="Flattened list of offers across sites.")
    best_offer: Optional[PriceOffer] = Field(None, description="Cheapest offer among parsed prices.")
    cached: bool = Field(False, description="True if results were served from cache.")
    generated_at: datetime = Field(..., description="UTC timestamp when response was generated.")


class CreateJobRequest(BaseModel):
    query: str = Field(..., min_length=2, max_length=200, description="Product name or search query.")
    priority: int = Field(5, ge=1, le=10, description="Higher priority jobs may run first (best-effort).")


class CreateJobResponse(BaseModel):
    job_id: str = Field(..., description="Job identifier.")
    status: Literal["queued"] = Field("queued", description="Initial job status.")


class JobStatusResponse(BaseModel):
    job_id: str = Field(..., description="Job identifier.")
    status: Literal["queued", "running", "succeeded", "failed"] = Field(..., description="Current job status.")
    progress: int = Field(0, ge=0, le=100, description="Best-effort progress percentage.")
    message: Optional[str] = Field(None, description="Optional status message.")
    result: Optional[ComparePricesResponse] = Field(None, description="Result if succeeded.")
    updated_at: datetime = Field(..., description="UTC timestamp of last update.")


class PriceHistoryResponse(BaseModel):
    query: str = Field(..., description="Query being examined.")
    items: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Historical snapshots. Each item contains at least: ts, site, title, price_inr, url.",
    )
