from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from quant_system.db.database import Base


class AuditMixin:
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False, default="system")
    updated_by: Mapped[str] = mapped_column(String(64), nullable=False, default="system")


class StockBasicModel(AuditMixin, Base):
    __tablename__ = "stock_basic"

    id: Mapped[int | None] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts_code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    area: Mapped[str | None] = mapped_column(String(64))
    industry: Mapped[str | None] = mapped_column(String(128))
    market: Mapped[str | None] = mapped_column(String(64))
    exchange: Mapped[str | None] = mapped_column(String(16), index=True)
    list_date: Mapped[str | None] = mapped_column(String(32))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="tushare")


class StockPoolModel(AuditMixin, Base):
    __tablename__ = "stock_pools"

    id: Mapped[int | None] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    pool_type: Mapped[str] = mapped_column(String(32), nullable=False)


class StockPoolMemberModel(AuditMixin, Base):
    __tablename__ = "stock_pool_members"
    __table_args__ = (UniqueConstraint("pool_code", "symbol", name="uq_stock_pool_member"),)

    id: Mapped[int | None] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pool_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(128))
    reason: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="manual")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class StockKlineModel(AuditMixin, Base):
    __tablename__ = "stock_klines"
    __table_args__ = (
        UniqueConstraint("symbol", "period", "trade_time", name="uq_stock_kline"),
        Index("ix_stock_klines_symbol_period_time", "symbol", "period", "trade_time"),
        Index("ix_stock_klines_period_time", "period", "trade_time"),
    )

    id: Mapped[int | None] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    period: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    trade_time: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    open: Mapped[float | None] = mapped_column("open", Float, quote=True)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float | None] = mapped_column("close", Float, quote=True)
    volume: Mapped[float | None] = mapped_column(Float)
    amount: Mapped[float | None] = mapped_column(Float)
    change_pct: Mapped[float | None] = mapped_column(Float)
    turnover_rate: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="akshare")


class KlineSyncLogModel(AuditMixin, Base):
    __tablename__ = "kline_sync_logs"

    id: Mapped[int | None] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pool_code: Mapped[str | None] = mapped_column(String(64), index=True)
    symbol: Mapped[str | None] = mapped_column(String(32), index=True)
    period: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    rows_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    message: Mapped[str | None] = mapped_column(Text)


class StockFeatureModel(AuditMixin, Base):
    __tablename__ = "stock_features"
    __table_args__ = (
        UniqueConstraint("symbol", "period", "trade_time", name="uq_stock_feature"),
        Index("ix_stock_features_symbol_period_time", "symbol", "period", "trade_time"),
        Index("ix_stock_features_period_time", "period", "trade_time"),
    )

    id: Mapped[int | None] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    period: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    trade_time: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    close: Mapped[float | None] = mapped_column("close", Float, quote=True)
    ma5: Mapped[float | None] = mapped_column(Float)
    ma10: Mapped[float | None] = mapped_column(Float)
    ma20: Mapped[float | None] = mapped_column(Float)
    ma60: Mapped[float | None] = mapped_column(Float)
    return_1: Mapped[float | None] = mapped_column(Float)
    return_5: Mapped[float | None] = mapped_column(Float)
    return_20: Mapped[float | None] = mapped_column(Float)
    volatility_20: Mapped[float | None] = mapped_column(Float)
    volume_ratio_5: Mapped[float | None] = mapped_column(Float)
    price_position_20: Mapped[float | None] = mapped_column(Float)
    price_position_60: Mapped[float | None] = mapped_column(Float)
    trend_direction: Mapped[str | None] = mapped_column(String(64))
    trend_score: Mapped[float | None] = mapped_column(Float)
    signal: Mapped[str | None] = mapped_column("signal", String(64), quote=True)


class PaperAccountModel(AuditMixin, Base):
    __tablename__ = "paper_accounts"

    account_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    initial_cash: Mapped[float] = mapped_column(Float, nullable=False)
    cash: Mapped[float] = mapped_column(Float, nullable=False)
    realized_pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0)


class PaperPositionModel(Base):
    __tablename__ = "paper_positions"

    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    avg_price: Mapped[float] = mapped_column(Float, nullable=False)
    opened_at: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False, default="system")
    updated_by: Mapped[str] = mapped_column(String(64), nullable=False, default="system")


class PaperOrderModel(AuditMixin, Base):
    __tablename__ = "paper_orders"
    __table_args__ = (
        Index("ix_paper_orders_created_at", "created_at"),
        Index("ix_paper_orders_side_status_created", "side", "status", "created_at"),
        Index("ix_paper_orders_strategy_side_status_created", "strategy_mode", "side", "status", "created_at"),
        Index("ix_paper_orders_symbol_created", "symbol", "created_at"),
    )

    id: Mapped[int | None] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(16), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    reason: Mapped[str | None] = mapped_column(Text)
    strategy_mode: Mapped[str | None] = mapped_column(String(32), index=True)
    decision_json: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    source_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    source_action: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_memo: Mapped[str | None] = mapped_column(Text, nullable=True)
    audit_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    gross_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    commission: Mapped[float | None] = mapped_column(Float, nullable=True)
    stamp_duty: Mapped[float | None] = mapped_column(Float, nullable=True)
    transfer_fee: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_fee: Mapped[float | None] = mapped_column(Float, nullable=True)
    realized_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)


class PaperCashFlowModel(AuditMixin, Base):
    __tablename__ = "paper_cash_flows"
    __table_args__ = (
        Index("ix_paper_cash_flows_symbol_created", "symbol", "created_at"),
    )

    id: Mapped[int | None] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str | None] = mapped_column(String(128), index=True)
    symbol: Mapped[str | None] = mapped_column(String(32), index=True)
    side: Mapped[str] = mapped_column(String(16), nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    cash_after: Mapped[float] = mapped_column(Float, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)


class BacktestRunModel(AuditMixin, Base):
    __tablename__ = "backtest_runs"

    id: Mapped[int | None] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    scope: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    symbol: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    pool_code: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    period: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    strategy_mode: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    start_date: Mapped[str | None] = mapped_column(String(32), nullable=True)
    end_date: Mapped[str | None] = mapped_column(String(32), nullable=True)
    initial_cash: Mapped[float | None] = mapped_column(Float, nullable=True)
    quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rows_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tested_bars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    trade_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    round_trip_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_pnl_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_equity: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_drawdown: Mapped[float | None] = mapped_column(Float, nullable=True)
    win_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    summary_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    params_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    rule_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    execution_rules_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class BacktestTradeModel(AuditMixin, Base):
    __tablename__ = "backtest_trades"

    id: Mapped[int | None] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    period: Mapped[str] = mapped_column(String(32), nullable=False)
    trade_time: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    accepted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    requested_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_fee: Mapped[float | None] = mapped_column(Float, nullable=True)
    realized_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class BacktestEquityModel(AuditMixin, Base):
    __tablename__ = "backtest_equity_curve"

    id: Mapped[int | None] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    period: Mapped[str] = mapped_column(String(32), nullable=False)
    trade_time: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    cash: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    equity: Mapped[float | None] = mapped_column(Float, nullable=True)
    realized_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    unrealized_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)


class MarketNewsModel(AuditMixin, Base):
    __tablename__ = "market_news"
    __table_args__ = (
        UniqueConstraint("fingerprint", name="uq_market_news_fingerprint"),
        Index("ix_market_news_type_published", "news_type", "published_at"),
        Index("ix_market_news_source_published", "source", "published_at"),
    )

    id: Mapped[int | None] = mapped_column(Integer, primary_key=True, autoincrement=True)
    news_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    fingerprint: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    news_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    published_at: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    fetched_at: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    related_symbols: Mapped[str | None] = mapped_column(Text, nullable=True)
    related_sectors: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[str | None] = mapped_column(Text, nullable=True)
    sentiment: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    importance: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class NewsRAGChunkModel(AuditMixin, Base):
    __tablename__ = "news_rag_chunks"
    __table_args__ = (
        UniqueConstraint("chunk_id", name="uq_news_rag_chunks_chunk_id"),
        UniqueConstraint("news_id", "chunk_index", name="uq_news_rag_chunks_news_index"),
        Index("ix_news_rag_chunks_vector", "vector_store", "collection_name"),
    )

    id: Mapped[int | None] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chunk_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    news_id: Mapped[str] = mapped_column(String(128), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    text_preview: Mapped[str | None] = mapped_column(String(512), nullable=True)
    token_estimate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    vector_store: Mapped[str | None] = mapped_column(String(64), nullable=True)
    collection_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    vector_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class AIObservationCandidateModel(AuditMixin, Base):
    __tablename__ = "ai_observation_candidates"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_ai_observation_candidates_dedupe_key"),
        Index("ix_ai_observation_symbol_status", "symbol", "status"),
        Index("ix_ai_observation_pool_scan", "pool_code", "scan_id"),
        Index("ix_ai_observation_status_score", "status", "recommendation_score"),
        Index("ix_ai_observation_next_check", "status", "next_check_at"),
    )

    id: Mapped[int | None] = mapped_column(Integer, primary_key=True, autoincrement=True)
    candidate_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    scan_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    dedupe_key: Mapped[str] = mapped_column(String(256), nullable=False, unique=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    stock_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    pool_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False, default="pool_scan", index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="watching", index=True)
    recommendation_score: Mapped[float | None] = mapped_column(Float, nullable=True, index=True)
    ai_action: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_level: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    reasons_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_notes_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    suggested_next_step: Mapped[str | None] = mapped_column(Text, nullable=True)
    trigger_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    analysis_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    linked_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    tracking_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_reviewed_at: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    last_tracked_at: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    next_check_at: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    trigger_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    status_changed_at: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class AIAnalysisRecordModel(AuditMixin, Base):
    __tablename__ = "ai_analysis_records"
    __table_args__ = (
        Index("ix_ai_analysis_symbol_created", "symbol", "created_at"),
        Index("ix_ai_analysis_type_status_created", "analysis_type", "status", "created_at"),
    )

    id: Mapped[int | None] = mapped_column(Integer, primary_key=True, autoincrement=True)
    analysis_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    analysis_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    action: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_level: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    plan_execution: Mapped[str | None] = mapped_column(Text, nullable=True)
    plan_position_size: Mapped[str | None] = mapped_column(Text, nullable=True)
    plan_entry_condition: Mapped[str | None] = mapped_column(Text, nullable=True)
    plan_watch_condition: Mapped[str | None] = mapped_column(Text, nullable=True)
    plan_stop_loss: Mapped[str | None] = mapped_column(Text, nullable=True)
    plan_take_profit: Mapped[str | None] = mapped_column(Text, nullable=True)
    plan_invalid_condition: Mapped[str | None] = mapped_column(Text, nullable=True)
    plan_review_time: Mapped[str | None] = mapped_column(Text, nullable=True)
    plan_next_step: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_constraint_triggered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    risk_forced_action: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    risk_original_action: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    risk_trigger_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_original_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_final_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_constraint_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    linked_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    linked_order_status: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    linked_order_side: Mapped[str | None] = mapped_column(String(16), nullable=True)
    linked_order_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    linked_order_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    linked_order_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    linked_order_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False, default="v1")
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    input_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    context_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class AlertTodoModel(AuditMixin, Base):
    __tablename__ = "alert_todos"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_alert_todos_dedupe_key"),
        Index("ix_alert_todos_status_severity", "status", "severity"),
        Index("ix_alert_todos_symbol_status", "symbol", "status"),
        Index("ix_alert_todos_source", "source_type", "source_id"),
    )

    id: Mapped[int | None] = mapped_column(Integer, primary_key=True, autoincrement=True)
    todo_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    dedupe_key: Mapped[str] = mapped_column(String(256), nullable=False, unique=True)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    stock_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    severity: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open", index=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    suggested_action: Mapped[str | None] = mapped_column(String(64), nullable=True)
    suggested_direction: Mapped[str | None] = mapped_column(String(16), nullable=True)
    suggested_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    action_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    analysis_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    linked_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    snooze_until: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    acknowledged_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resolved_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ignored_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class AIChatMessageModel(AuditMixin, Base):
    """AI 多轮对话消息记录。

    每条消息关联一个 session_id（对话会话）和原始 analysis_id。
    role: user / assistant / system
    """
    __tablename__ = "ai_chat_messages"
    __table_args__ = (
        Index("ix_ai_chat_session_seq", "session_id", "seq"),
    )

    id: Mapped[int | None] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    analysis_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)


class TaskExecutionRecordModel(AuditMixin, Base):
    __tablename__ = "task_execution_records"

    id: Mapped[int | None] = mapped_column(Integer, primary_key=True, autoincrement=True)
    execution_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    task_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    trigger_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    started_at: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    finished_at: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    params_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_summary_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    candidate_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    success_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    failed_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    accepted_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rejected_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    order_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
