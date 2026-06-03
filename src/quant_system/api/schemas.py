from pydantic import BaseModel, Field


class TradeSourceAudit(BaseModel):
    source_type: str | None = Field(default=None, description="交易来源类型：manual / ai_analysis / risk_warning / ai_observation")
    source_id: str | None = Field(default=None, description="来源记录 ID，例如 AI 分析 ID")
    source_action: str | None = Field(default=None, description="来源建议动作")
    source_confidence: float | None = Field(default=None, ge=0, le=1, description="来源建议置信度")
    source_memo: str | None = Field(default=None, description="来源摘要、风控状态或执行说明")


class OpenPositionRequest(BaseModel):
    symbol: str = Field(..., examples=["600519.SH"])
    quantity: int = Field(..., gt=0)
    max_price: float | None = Field(default=None, gt=0)
    strategy: str = Field(default="manual", description="手动交易归类策略模式，默认 manual；manual_open 属于来源，不作为策略模式")
    force: bool = Field(default=False, description="策略不建议手动买入时，是否由前端二次确认后强制提交")
    audit: TradeSourceAudit | None = Field(default=None, description="交易来源审计信息")


class ClosePositionRequest(BaseModel):
    symbol: str = Field(..., examples=["600519.SH"])
    quantity: int | None = Field(default=None, gt=0)
    min_price: float | None = Field(default=None, gt=0)
    reason: str = "scheduled_close"
    audit: TradeSourceAudit | None = Field(default=None, description="交易来源审计信息")


class StockPoolMemberCreate(BaseModel):
    symbol: str = Field(..., examples=["600519"])
    name: str | None = Field(default=None, examples=["贵州茅台"])
    reason: str | None = Field(default=None, examples=["前端手动加入自选池"])
    tags: list[str] = Field(default_factory=list, examples=[["白酒", "核心资产"]])
    source: str = "manual"


class StockPoolMemberUpdate(BaseModel):
    name: str | None = None
    reason: str | None = None
    tags: list[str] | None = None
    enabled: bool | None = None


class KlinePoolSyncRequest(BaseModel):
    periods: list[str] = Field(default_factory=lambda: ["daily", "minute"])
    limit_symbols: int | None = Field(default=None, gt=0)


class KlineSymbolSyncRequest(BaseModel):
    period: str = "daily"


class FeaturePoolComputeRequest(BaseModel):
    period: str = "daily"
    limit_symbols: int | None = Field(default=None, gt=0)


class FeatureSymbolComputeRequest(BaseModel):
    period: str = "daily"


class NewsSyncRequest(BaseModel):
    news_types: list[str] | None = Field(default_factory=lambda: ["news", "notice"], examples=[["news", "notice"]])
    limit: int = Field(default=50, gt=0, le=500)
    force_refresh: bool = Field(default=False, description="是否跳过资讯源缓存，强制重新拉取外部数据源")


class AutoBuyRequest(BaseModel):
    pools: list[str] | None = Field(default=None, examples=[["favorites", "candidates"]])
    limit_symbols: int | None = Field(default=None, gt=0)
    strategy_mode: str = Field(default="strict", pattern="^(strict|normal|loose)$", examples=["strict"])


class AutoCloseRequest(BaseModel):
    strategy_mode: str = Field(default="strict", pattern="^(strict|normal|loose)$", examples=["strict"])
    mode: str = Field(default="risk_scan", pattern="^(risk_scan|force_close_all)$", description="risk_scan=只按止损/止盈/趋势风控平仓；force_close_all=定时或手动强制清仓")
    dry_run: bool = Field(default=False, description="预演模式：只返回将要平仓的股票、数量和原因，不实际下单")
    scheduled: bool | None = Field(default=None, description="兼容旧字段：true 等价于 force_close_all，false 等价于 risk_scan")


class AlertTodoUpdateRequest(BaseModel):
    status: str | None = Field(default=None, pattern="^(open|acknowledged|reviewing|snoozed|resolved|ignored)$")
    note: str | None = Field(default=None, max_length=1000)
    snooze_until: str | None = Field(default=None, description="ISO 时间字符串，到期前首页不展示该待办")
    linked_order_id: str | None = Field(default=None, description="关联的模拟订单 ID")


class BacktestRequest(BaseModel):
    symbol: str | None = Field(default=None, examples=["600519.SH"])
    pool_code: str | None = Field(default=None, examples=["favorites"])
    period: str = "daily"
    strategy_mode: str = Field(default="strict", pattern="^(strict|normal|loose)$", examples=["strict"])
    initial_cash: float | None = Field(default=None, gt=0)
    quantity: int | None = Field(default=None, gt=0)
    start_date: str | None = Field(default=None, examples=["2024-01-01"])
    end_date: str | None = Field(default=None, examples=["2026-05-26"])
    limit_symbols: int | None = Field(default=None, gt=0)


class BacktestStrategyCompareRequest(BaseModel):
    symbol: str | None = Field(default=None, examples=["600519.SH"])
    pool_code: str | None = Field(default=None, examples=["favorites"])
    period: str = "daily"
    strategy_modes: list[str] = Field(default_factory=lambda: ["strict", "normal", "loose"], examples=[["strict", "normal", "loose"]])
    initial_cash: float | None = Field(default=None, gt=0)
    quantity: int | None = Field(default=None, gt=0)
    start_date: str | None = Field(default=None, examples=["2024-01-01"])
    end_date: str | None = Field(default=None, examples=["2026-05-26"])
    limit_symbols: int | None = Field(default=None, gt=0)


class BacktestGridOptimizeRequest(BaseModel):
    symbol: str | None = Field(default=None, examples=["600519.SH"])
    pool_code: str | None = Field(default=None, examples=["favorites"])
    period: str = "daily"
    strategy_mode: str = Field(default="loose", pattern="^(strict|normal|loose)$", examples=["loose"])
    initial_cash: float | None = Field(default=None, gt=0)
    quantity: int | None = Field(default=None, gt=0)
    start_date: str | None = Field(default=None, examples=["2024-01-01"])
    end_date: str | None = Field(default=None, examples=["2026-05-26"])
    limit_symbols: int | None = Field(default=None, gt=0)
    take_profit_pct: list[float] = Field(default_factory=lambda: [0.04, 0.06, 0.08])
    stop_loss_pct: list[float] = Field(default_factory=lambda: [0.04, 0.06])
    min_trend_score: list[float] = Field(default_factory=lambda: [50.0, 56.0])
    min_confidence: list[float] = Field(default_factory=lambda: [0.45, 0.52])
    max_combinations: int = Field(default=24, gt=0, le=100)
