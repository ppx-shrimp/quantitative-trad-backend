from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class AIStockAnalysisRequest(BaseModel):
    symbol: str = Field(..., min_length=1, examples=["600519.SH"])
    analysis_type: Literal["buy_decision", "position_review", "risk_review"] = "buy_decision"
    horizon: str = Field(default="1-5d", description="分析周期，例如 1-5d / intraday / 1-2w")
    include_news: bool = True
    include_position: bool = True
    user_question: str | None = Field(default=None, description="用户额外关注点")


class AIStockDecision(BaseModel):
    action: Literal["buy", "watch", "avoid", "hold", "reduce", "sell"] = "watch"
    confidence: float = Field(default=0.5, ge=0, le=1)
    risk_level: Literal["low", "medium", "high"] = "medium"
    summary: str
    reasons: list[str] = Field(default_factory=list)
    risk_warnings: list[str] = Field(default_factory=list)
    suggested_plan: dict[str, Any] = Field(default_factory=dict)
    data_quality: dict[str, Any] = Field(default_factory=dict)


class AIStockAnalysisResponse(BaseModel):
    analysis_id: str
    symbol: str
    analysis_type: str
    status: Literal["success", "failed"]
    provider: str
    model: str
    prompt_version: str
    decision: AIStockDecision | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    raw_output: dict[str, Any] | None = None
    error_message: str | None = None
    created_at: str
