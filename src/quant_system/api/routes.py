from difflib import SequenceMatcher

from fastapi import APIRouter, BackgroundTasks, Body, HTTPException, Query
from fastapi.responses import Response

from typing import TYPE_CHECKING

from quant_system.api.pagination import resolve_page_params
from quant_system.ai.schemas import AIStockAnalysisRequest
from quant_system.api.schemas import AlertTodoUpdateRequest, AutoBuyRequest, AutoCloseRequest, BacktestGridOptimizeRequest, BacktestRequest, BacktestStrategyCompareRequest, ClosePositionRequest, FeaturePoolComputeRequest, FeatureSymbolComputeRequest, KlinePoolSyncRequest, KlineSymbolSyncRequest, NewsSyncRequest, OpenPositionRequest, StockPoolMemberCreate, StockPoolMemberUpdate

if TYPE_CHECKING:
    from quant_system.ai.observation_service import AIObservationService
    from quant_system.ai.recommendation_service import AIRecommendationService
    from quant_system.ai.runtime_service import AIRuntimeService
    from quant_system.ai.service import AIAnalysisService
    from quant_system.services.alert_todo_service import AlertTodoService
    from quant_system.services.analysis_service import AnalysisService
    from quant_system.services.backtest_service import BacktestService
    from quant_system.services.data_readiness_service import DataReadinessService
    from quant_system.services.feature_service import FeatureService
    from quant_system.services.kline_service import KlineService
    from quant_system.services.market_index_service import MarketIndexService
    from quant_system.services.news_service import NewsService
    from quant_system.rag.news_chunk_service import NewsChunkService
    from quant_system.rag.news_embedding_service import NewsEmbeddingService
    from quant_system.rag.news_semantic_service import NewsSemanticService
    from quant_system.rag.service import RAGService
    from quant_system.services.prediction_service import PredictionService
    from quant_system.services.stock_basic_service import StockBasicService
    from quant_system.services.stock_pool_service import StockPoolService
    from quant_system.services.task_execution_service import TaskExecutionService
    from quant_system.services.trading_service import TradingService

router = APIRouter()
_ai_analysis_service: "AIAnalysisService | None" = None
_ai_recommendation_service: "AIRecommendationService | None" = None
_ai_observation_service: "AIObservationService | None" = None
_ai_runtime_service: "AIRuntimeService | None" = None
_ai_evaluation_service: "AIAnalysisEvaluationService | None" = None
_alert_todo_service: "AlertTodoService | None" = None
_analysis_service: "AnalysisService | None" = None
_backtest_service: "BacktestService | None" = None
_prediction_service: "PredictionService | None" = None
_news_service: "NewsService | None" = None
_trading_service: "TradingService | None" = None
_stock_basic_service: "StockBasicService | None" = None
_stock_pool_service: "StockPoolService | None" = None
_kline_service: "KlineService | None" = None
_feature_service: "FeatureService | None" = None
_data_readiness_service: "DataReadinessService | None" = None
_task_execution_service: "TaskExecutionService | None" = None
_market_index_service: "MarketIndexService | None" = None
_dashboard_cache_service: "DashboardCacheService | None" = None
_rag_service: "RAGService | None" = None
_news_chunk_service: "NewsChunkService | None" = None
_news_embedding_service: "NewsEmbeddingService | None" = None
_news_semantic_service: "NewsSemanticService | None" = None


def get_news_chunk_service() -> "NewsChunkService":
    global _news_chunk_service
    if _news_chunk_service is None:
        from quant_system.rag.news_chunk_service import NewsChunkService

        _news_chunk_service = NewsChunkService()
    return _news_chunk_service


def get_rag_service() -> "RAGService":
    global _rag_service
    if _rag_service is None:
        from quant_system.rag.service import RAGService

        _rag_service = RAGService()
    return _rag_service


def get_news_embedding_service() -> "NewsEmbeddingService":
    global _news_embedding_service
    if _news_embedding_service is None:
        from quant_system.rag.news_embedding_service import NewsEmbeddingService

        _news_embedding_service = NewsEmbeddingService()
    return _news_embedding_service


def get_news_semantic_service() -> "NewsSemanticService":
    global _news_semantic_service
    if _news_semantic_service is None:
        from quant_system.rag.news_semantic_service import NewsSemanticService

        _news_semantic_service = NewsSemanticService()
    return _news_semantic_service


def get_ai_analysis_service() -> "AIAnalysisService":
    global _ai_analysis_service
    if _ai_analysis_service is None:
        from quant_system.ai.service import AIAnalysisService

        _ai_analysis_service = AIAnalysisService()
    return _ai_analysis_service


def get_ai_recommendation_service() -> "AIRecommendationService":
    global _ai_recommendation_service
    if _ai_recommendation_service is None:
        from quant_system.ai.recommendation_service import AIRecommendationService

        _ai_recommendation_service = AIRecommendationService()
    return _ai_recommendation_service


def get_ai_observation_service() -> "AIObservationService":
    global _ai_observation_service
    if _ai_observation_service is None:
        from quant_system.ai.observation_service import AIObservationService

        _ai_observation_service = AIObservationService()
    return _ai_observation_service


def get_ai_runtime_service() -> "AIRuntimeService":
    global _ai_runtime_service
    if _ai_runtime_service is None:
        from quant_system.ai.runtime_service import AIRuntimeService

        _ai_runtime_service = AIRuntimeService()
    return _ai_runtime_service


def get_alert_todo_service() -> "AlertTodoService":
    global _alert_todo_service
    if _alert_todo_service is None:
        from quant_system.services.alert_todo_service import AlertTodoService

        _alert_todo_service = AlertTodoService()
    return _alert_todo_service


def get_ai_evaluation_service() -> "AIAnalysisEvaluationService":
    global _ai_evaluation_service
    if _ai_evaluation_service is None:
        from quant_system.ai.evaluation_service import AIAnalysisEvaluationService

        _ai_evaluation_service = AIAnalysisEvaluationService(kline_service=get_kline_service())
    return _ai_evaluation_service


def get_analysis_service() -> "AnalysisService":
    global _analysis_service
    if _analysis_service is None:
        from quant_system.services.analysis_service import AnalysisService

        _analysis_service = AnalysisService()
    return _analysis_service


def get_backtest_service() -> "BacktestService":
    global _backtest_service
    if _backtest_service is None:
        from quant_system.services.backtest_service import BacktestService

        _backtest_service = BacktestService()
    return _backtest_service


def get_prediction_service() -> "PredictionService":
    global _prediction_service
    if _prediction_service is None:
        from quant_system.services.prediction_service import PredictionService

        _prediction_service = PredictionService()
    return _prediction_service


def get_news_service() -> "NewsService":
    global _news_service
    if _news_service is None:
        from quant_system.services.news_service import NewsService

        _news_service = NewsService()
    return _news_service


def get_trading_service() -> "TradingService":
    global _trading_service
    if _trading_service is None:
        from quant_system.services.trading_service import TradingService

        _trading_service = TradingService()
    return _trading_service


def get_stock_basic_service() -> "StockBasicService":
    global _stock_basic_service
    if _stock_basic_service is None:
        from quant_system.services.stock_basic_service import StockBasicService

        _stock_basic_service = StockBasicService()
    return _stock_basic_service


def get_stock_pool_service() -> "StockPoolService":
    global _stock_pool_service
    if _stock_pool_service is None:
        from quant_system.services.stock_pool_service import StockPoolService

        _stock_pool_service = StockPoolService()
    return _stock_pool_service


def get_kline_service() -> "KlineService":
    global _kline_service
    if _kline_service is None:
        from quant_system.services.kline_service import KlineService

        _kline_service = KlineService()
    return _kline_service


def get_feature_service() -> "FeatureService":
    global _feature_service
    if _feature_service is None:
        from quant_system.services.feature_service import FeatureService

        _feature_service = FeatureService()
    return _feature_service


def get_data_readiness_service() -> "DataReadinessService":
    global _data_readiness_service
    if _data_readiness_service is None:
        from quant_system.services.data_readiness_service import DataReadinessService

        _data_readiness_service = DataReadinessService()
    return _data_readiness_service


def get_task_execution_service() -> "TaskExecutionService":
    global _task_execution_service
    if _task_execution_service is None:
        from quant_system.services.task_execution_service import TaskExecutionService

        _task_execution_service = TaskExecutionService()
    return _task_execution_service


def get_market_index_service() -> "MarketIndexService":
    global _market_index_service
    if _market_index_service is None:
        from quant_system.services.market_index_service import MarketIndexService

        _market_index_service = MarketIndexService()
    return _market_index_service


def get_dashboard_cache_service() -> "DashboardCacheService":
    global _dashboard_cache_service
    if _dashboard_cache_service is None:
        from quant_system.services.dashboard_cache_service import DashboardCacheService

        _dashboard_cache_service = DashboardCacheService()
    return _dashboard_cache_service


def _compact_keyword(value: object) -> str:
    return str(value or "").strip().lower().replace(".", "").replace(" ", "")


def _stock_match_score(item: dict, keyword: str) -> float:
    fields = [
        _compact_keyword(item.get("symbol")),
        _compact_keyword(item.get("ts_code")),
        _compact_keyword(item.get("code")),
        _compact_keyword(item.get("name")),
    ]
    fields = [field for field in fields if field]
    if not fields:
        return 0.0
    if any(field == keyword for field in fields):
        return 1.0
    if any(field.startswith(keyword) for field in fields):
        return 0.95
    if any(keyword in field for field in fields):
        return 0.9
    return max(SequenceMatcher(None, keyword, field).ratio() for field in fields)


@router.get("/rag/status")
def rag_status() -> dict:
    return get_rag_service().status()


@router.post("/rag/collections/ensure")
def rag_ensure_collection(payload: dict = Body(default_factory=dict)) -> dict:
    force_recreate = bool(payload.get("force_recreate") or payload.get("force") or False)
    return get_rag_service().ensure_collection(force_recreate=force_recreate)


@router.post("/rag/news/chunk")
def rag_chunk_news(payload: dict = Body(default_factory=dict)) -> dict:
    news_id = str(payload.get("news_id") or "").strip()
    force_rechunk = bool(payload.get("force_rechunk") or False)
    if news_id:
        return get_news_chunk_service().chunk_news_by_id(news_id, force_rechunk=force_rechunk)
    try:
        limit = int(payload.get("limit") or 50)
    except (TypeError, ValueError):
        limit = 50
    return get_news_chunk_service().chunk_recent_news(limit=limit, force_rechunk=force_rechunk)


@router.get("/rag/news/chunks/stats")
def rag_news_chunk_stats() -> dict:
    return get_news_embedding_service().chunk_stats()


@router.get("/rag/news/chunks")
def rag_list_news_chunks(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    news_id: str | None = Query(default=None),
    embedded: bool | None = Query(default=None, description="true=只看已向量化，false=只看待向量化，不传=全部"),
) -> dict:
    return get_news_embedding_service().list_chunks(
        page=page,
        page_size=page_size,
        news_id=news_id,
        embedded=embedded,
    )


@router.post("/rag/news/semantic/analyze")
def rag_analyze_news_semantic(payload: dict = Body(default_factory=dict)) -> dict:
    news_id = str(payload.get("news_id") or "").strip()
    chunk_id = str(payload.get("chunk_id") or "").strip()
    text = str(payload.get("text") or "").strip()
    use_llm = bool(payload.get("use_llm", True))
    if news_id:
        return get_news_semantic_service().analyze_news(news_id=news_id, use_llm=use_llm)
    if chunk_id:
        return get_news_semantic_service().analyze_chunk(chunk_id=chunk_id, use_llm=use_llm)
    return get_news_semantic_service().analyze_text(text=text, use_llm=use_llm)


@router.post("/rag/news/embed")
def rag_embed_news(payload: dict = Body(default_factory=dict)) -> dict:
    news_id = str(payload.get("news_id") or "").strip() or None
    force_reembed = bool(payload.get("force_reembed") or False)
    try:
        limit = int(payload.get("limit") or 50)
    except (TypeError, ValueError):
        limit = 50
    return get_news_embedding_service().embed_news_chunks(
        limit=limit,
        news_id=news_id,
        force_reembed=force_reembed,
    )


@router.get("/rag/news/search")
def rag_search_news(
    query: str = Query(..., min_length=1),
    limit: int = Query(default=8, ge=1, le=20),
    score_threshold: float | None = Query(default=None, ge=0, le=1),
    related_symbol: str | None = Query(default=None, description="按相关股票过滤"),
    related_sector: str | None = Query(default=None, description="按相关板块过滤"),
    sentiment: str | None = Query(default=None, description="按情绪过滤：positive/negative/neutral"),
    dedupe_by_news: bool = Query(default=True, description="是否按 news_id 去重"),
) -> dict:
    return get_news_embedding_service().search_news_chunks(
        query=query,
        limit=limit,
        score_threshold=score_threshold,
        related_symbol=related_symbol,
        related_sector=related_sector,
        sentiment=sentiment,
        dedupe_by_news=dedupe_by_news,
    )


@router.get("/rag/news/context")
def rag_news_context(
    query: str = Query(..., min_length=1),
    limit: int = Query(default=5, ge=1, le=12),
    score_threshold: float | None = Query(default=None, ge=0, le=1),
    related_symbol: str | None = Query(default=None),
    related_sector: str | None = Query(default=None),
    sentiment: str | None = Query(default=None),
) -> dict:
    return get_news_embedding_service().build_retrieval_context(
        query=query,
        limit=limit,
        score_threshold=score_threshold,
        related_symbol=related_symbol,
        related_sector=related_sector,
        sentiment=sentiment,
    )


@router.post("/rag/debug/upsert")
def rag_debug_upsert(payload: dict = Body(...)) -> dict:
    text = str(payload.get("text") or "")
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    return get_rag_service().debug_upsert(text, metadata=metadata)


@router.get("/rag/debug/search")
def rag_debug_search(query: str = Query(..., min_length=1), limit: int = Query(default=8, ge=1, le=20)) -> dict:
    return get_rag_service().debug_search(query, limit=limit)


@router.get("/ai/llm/status")
def ai_llm_status() -> dict:
    return get_ai_analysis_service().llm_status()


@router.post("/ai/llm/diagnose")
def ai_llm_diagnose() -> dict:
    return get_ai_analysis_service().diagnose_llm()


@router.get("/ai/runtime/status")
def ai_runtime_status() -> dict:
    """AI / RAG 运行诊断中心：聚合 LLM、RAG、Embedding、Qdrant 和新闻向量化状态。"""
    return get_ai_runtime_service().status()


@router.post("/rag/news/pipeline/run")
def rag_news_pipeline_run(payload: dict = Body(default_factory=dict)) -> dict:
    """一键 RAG 新闻预处理：collection 初始化、新闻 chunk、embedding、向量入库。"""
    return _run_rag_pipeline_from_payload(payload)


@router.post("/rag/news/pipeline/tasks")
def rag_news_pipeline_task(background_tasks: BackgroundTasks, payload: dict = Body(default_factory=dict)) -> dict:
    """创建轻量后台任务执行 RAG 新闻预处理，返回 task_execution_id 供前端轮询。"""
    params = _rag_pipeline_params(payload)
    execution_id = get_task_execution_service().start_task(
        task_name="rag_news_pipeline",
        task_type="ai_rag",
        trigger_type="manual_api",
        params=params,
    )
    background_tasks.add_task(_run_rag_pipeline_task, execution_id, params)
    return {"ok": True, "task_execution_id": execution_id, "execution_id": execution_id, "status": "running", "params": params}


@router.get("/rag/news/pipeline/tasks/{execution_id}")
def rag_news_pipeline_task_status(execution_id: str) -> dict:
    return get_task_execution_service().get_execution(execution_id)


def _run_rag_pipeline_from_payload(payload: dict) -> dict:
    params = _rag_pipeline_params(payload)
    return get_ai_runtime_service().run_rag_pipeline(**params)


def _rag_pipeline_params(payload: dict) -> dict:
    try:
        limit = int(payload.get("limit") or 100)
    except (TypeError, ValueError):
        limit = 100
    return {
        "limit": limit,
        "force_rechunk": bool(payload.get("force_rechunk") or False),
        "force_reembed": bool(payload.get("force_reembed") or False),
        "ensure_collection": bool(payload.get("ensure_collection", True)),
        "run_embedding": bool(payload.get("run_embedding", True)),
    }


def _run_rag_pipeline_task(execution_id: str, params: dict) -> None:
    try:
        result = get_ai_runtime_service().run_rag_pipeline(**params)
        status = "success" if result.get("ok") else "failed"
        if result.get("status") in {"partial_failed", "partial_success"}:
            status = "partial_success"
        get_task_execution_service().finish_success(execution_id, result, status=status)
    except Exception as exc:
        get_task_execution_service().finish_failure(execution_id, exc)


@router.post("/ai/analyze-stock")
def ai_analyze_stock(request: AIStockAnalysisRequest) -> dict:
    return get_ai_analysis_service().analyze_stock(request).model_dump()


@router.get("/ai/recommendations")
def ai_stock_recommendations(
    pool_code: str = Query(default="favorites", description="股票池代码，第一版默认自选池"),
    limit: int = Query(default=5, ge=1, le=20),
    period: str = Query(default="daily", description="特征周期，默认 daily"),
    style: str = Query(default="steady_watch", pattern="^(steady_watch)$"),
) -> dict:
    return get_ai_recommendation_service().recommend_from_pool(
        pool_code=pool_code,
        limit=limit,
        period=period,
        style=style,
    )


@router.post("/ai/observations/scan")
def ai_observations_scan(payload: dict = Body(default_factory=dict)) -> dict:
    """AI 观察池扫描：从自选池/股票池生成候选，落库并进入状态流转。"""
    return get_ai_observation_service().scan_pool(
        pool_code=str(payload.get("pool_code") or "favorites"),
        limit=max(1, min(int(payload.get("limit") or 10), 50)),
        period=str(payload.get("period") or "daily"),
        style=str(payload.get("style") or "steady_watch"),
        run_deep_analysis=bool(payload.get("run_deep_analysis") or False),
    )


@router.get("/ai/observations")
def ai_observations(
    status: str | None = Query(default="active", description="active/all/watching/triggered/reviewing/dismissed/converted/archived"),
    pool_code: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    return get_ai_observation_service().list_candidates(status=status, pool_code=pool_code, limit=limit)


@router.post("/ai/observations/track")
def ai_observations_track(payload: dict = Body(default_factory=dict)) -> dict:
    """持续跟踪观察池活跃候选，根据最新 K 线触发状态流转。"""
    return get_ai_observation_service().track_candidates(
        limit=max(1, min(int(payload.get("limit") or 50), 200)),
        only_due=bool(payload.get("only_due", True)),
        run_deep_analysis_on_trigger=bool(payload.get("run_deep_analysis_on_trigger") or False),
    )


@router.patch("/ai/observations/{candidate_id}")
def ai_observation_update(candidate_id: str, payload: dict = Body(default_factory=dict)) -> dict:
    try:
        return get_ai_observation_service().update_candidate(
            candidate_id,
            status=payload.get("status"),
            note=payload.get("note"),
            linked_order_id=payload.get("linked_order_id"),
        )
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if "不存在" in message else 400
        raise HTTPException(status_code=status_code, detail=message) from exc


@router.post("/ai/observations/{candidate_id}/reanalyze")
def ai_observation_reanalyze(candidate_id: str) -> dict:
    try:
        return get_ai_observation_service().reanalyze_candidate(candidate_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/ai/observations/dify-advice")
def ai_observations_dify_advice() -> dict:
    return get_ai_observation_service().dify_advice()


@router.get("/ai/analysis-records")
def ai_analysis_records(
    symbol: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    include_payload: bool = Query(default=False, description="是否返回完整 input/context/output JSON"),
) -> dict:
    return get_ai_analysis_service().list_recent_records(symbol=symbol, limit=limit, include_payload=include_payload)


@router.get("/ai/analysis-evaluations")
def ai_analysis_evaluations(
    symbol: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    mode: str = Query(default="live", pattern="^(live|history)$"),
) -> dict:
    """批量复盘 AI 分析记录，按 1/3/5 个交易日计算风险收益综合命中率。"""
    if mode == "history":
        return get_ai_evaluation_service().evaluate_history_samples(symbol=symbol, limit=limit)
    return get_ai_evaluation_service().evaluate_records(symbol=symbol, limit=limit)


@router.get("/ai/analysis-evaluations/{analysis_id}")
def ai_analysis_evaluation_detail(analysis_id: str) -> dict:
    """复盘单条 AI 分析记录。"""
    return get_ai_evaluation_service().evaluate_one(analysis_id)


# ── AI 多轮对话 ──

_ai_chat_service: "AIChatService | None" = None


def get_ai_chat_service() -> "AIChatService":
    global _ai_chat_service
    if _ai_chat_service is None:
        from quant_system.ai.chat_service import AIChatService

        _ai_chat_service = AIChatService(llm_client=get_ai_analysis_service().llm_client)
    return _ai_chat_service


@router.post("/ai/chat/create")
def ai_chat_create(analysis_id: str = Body(..., embed=True)) -> dict:
    """基于分析记录创建对话会话。"""
    return get_ai_chat_service().create_session(analysis_id)


@router.post("/ai/chat/send")
def ai_chat_send(session_id: str = Body(...), message: str = Body(...)) -> dict:
    """发送追问消息，获取 AI 回复。"""
    return get_ai_chat_service().send_message(session_id, message)


@router.get("/ai/chat/messages")
def ai_chat_messages(session_id: str = Query(...)) -> dict:
    """获取对话会话的消息历史。"""
    return get_ai_chat_service().get_messages(session_id)


@router.get("/dashboard/snapshot")
def dashboard_snapshot(force_refresh: bool = False, allow_stale: bool = True) -> dict:
    return get_dashboard_cache_service().get_snapshot(force_refresh=force_refresh, allow_stale=allow_stale)


@router.post("/dashboard/prewarm")
def dashboard_prewarm(async_mode: bool = Query(default=True, description="是否后台异步预热")) -> dict:
    service = get_dashboard_cache_service()
    if async_mode:
        return service.prewarm_async(reason="manual_api")
    return service.prewarm(reason="manual_api")


@router.get("/market/indices")
def market_indices(force_refresh: bool = False) -> dict:
    return get_market_index_service().get_indices(force_refresh=force_refresh)


@router.get("/market/hot-sectors")
def market_hot_sectors(limit: int = Query(default=8, ge=1, le=30), force_refresh: bool = False) -> dict:
    return get_market_index_service().get_hot_sectors(limit=limit, force_refresh=force_refresh)


@router.get("/market/indices/diagnose")
def market_indices_diagnose() -> dict:
    return get_market_index_service().diagnose_connectivity()


@router.get("/stocks")
def list_stocks(
    page: int | None = Query(default=None, ge=1, description="页码，从 1 开始"),
    page_size: int | None = Query(default=None, ge=1, le=5000, description="每页条数"),
    limit: int | None = Query(default=None, ge=1, le=5000, description="兼容旧参数，等同 page_size"),
    keyword: str | None = Query(default=None, description="股票代码或名称关键字"),
    exclude_st: bool = Query(default=False, description="是否排除 ST、退市相关股票"),
    market: str | None = Query(default=None, description="市场板块：主板/创业板/科创板/北交所"),
) -> dict:
    try:
        items = get_stock_basic_service().list_stocks(exclude_st=exclude_st, market=market)
        if not items:
            items = get_analysis_service().market_data.get_stock_list()
            if exclude_st:
                items = [item for item in items if "ST" not in str(item.get("name") or "").upper() and "退" not in str(item.get("name") or "")]
            if market and market.strip():
                prefixes = get_stock_basic_service()._market_symbol_prefixes(market)
                items = [item for item in items if str(item.get("symbol") or item.get("code") or "").startswith(tuple(prefixes))]
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"股票列表数据源暂不可用：{exc}") from exc
    if keyword and keyword.strip():
        normalized_keyword = _compact_keyword(keyword)
        min_score = 0.45 if len(normalized_keyword) >= 3 else 0.9
        scored_items = [
            (score, item)
            for item in items
            if (score := _stock_match_score(item, normalized_keyword)) >= min_score
        ]
        items = [item for _, item in sorted(scored_items, key=lambda value: value[0], reverse=True)]
    params = resolve_page_params(page, page_size, limit, default_page_size=100)
    total = len(items)
    paged_items = items[params.offset : params.offset + params.limit]
    total_pages = (total + params.page_size - 1) // params.page_size if params.page_size else 0
    return {
        "items": paged_items,
        "total": total,
        "page": params.page,
        "page_size": params.page_size,
        "total_pages": total_pages,
        "count": len(paged_items),
    }


@router.get("/stocks/{symbol}/analysis")
def analyze_stock(symbol: str) -> dict:
    try:
        return get_analysis_service().analyze(symbol)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"股票分析依赖的行情数据源暂不可用：{exc}") from exc


@router.get("/stocks/{symbol}/kline")
def stock_kline(symbol: str, period: str = "daily", start_date: str | None = None, end_date: str | None = None) -> dict:
    try:
        result = get_kline_service().get_display_klines(
            symbol=symbol,
            period=period,
            start_date=start_date,
            end_date=end_date,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"K 线数据源暂不可用，请稍后重试或先使用本地 K 线接口：{exc}") from exc
    return result


@router.get("/stocks/{symbol}/prediction")
def predict_stock(symbol: str) -> dict:
    try:
        return get_prediction_service().predict_kline(symbol)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"走势预测依赖的行情数据源暂不可用：{exc}") from exc


@router.get("/news/hot")
def hot_news() -> dict:
    return get_news_service().get_hot_news()


@router.get("/news/latest")
def list_latest_news(
    news_type: str | None = Query(default=None, description="资讯类型：news/notice"),
    source: str | None = Query(default=None, description="资讯来源"),
    symbol: str | None = Query(default=None, description="关联股票代码"),
    keyword: str | None = Query(default=None, description="标题/摘要/标签关键词"),
    start_date: str | None = Query(default=None, description="起始发布时间"),
    end_date: str | None = Query(default=None, description="结束发布时间"),
    page: int | None = Query(default=None, ge=1, description="页码，从 1 开始"),
    page_size: int | None = Query(default=None, ge=1, le=500, description="每页条数"),
    limit: int | None = Query(default=None, ge=1, le=500, description="兼容旧参数，等同 page_size"),
) -> dict:
    params = resolve_page_params(page, page_size, limit, default_page_size=50)
    return get_news_service().list_news_page(
        page_params=params,
        news_type=news_type,
        source=source,
        symbol=symbol,
        keyword=keyword,
        start_date=start_date,
        end_date=end_date,
    ).to_dict()


@router.post("/news/sync")
def sync_news(request: NewsSyncRequest) -> dict:
    return get_news_service().sync_news(news_types=request.news_types, limit=request.limit, force_refresh=request.force_refresh)


@router.get("/news/sources/status")
def news_sources_status() -> dict:
    return get_news_service().source_status()


@router.post("/news/cache/invalidate")
def invalidate_news_cache() -> dict:
    return get_news_service().invalidate_cache()


@router.get("/stocks/{symbol}/news")
def list_symbol_news(
    symbol: str,
    news_type: str | None = Query(default=None, description="资讯类型：news/notice"),
    page: int | None = Query(default=None, ge=1, description="页码，从 1 开始"),
    page_size: int | None = Query(default=None, ge=1, le=500, description="每页条数"),
    limit: int | None = Query(default=None, ge=1, le=500, description="兼容旧参数，等同 page_size"),
) -> dict:
    params = resolve_page_params(page, page_size, limit, default_page_size=20)
    return get_news_service().list_symbol_news(symbol=symbol, page_params=params, news_type=news_type).to_dict()


@router.get("/pools")
def list_pools() -> dict:
    items = get_stock_pool_service().list_pools()
    return {"count": len(items), "items": items}


@router.get("/pools/{pool_code}/stocks")
def list_pool_members(pool_code: str, enabled_only: bool = True) -> dict:
    try:
        items = get_stock_pool_service().list_members(pool_code, enabled_only=enabled_only)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"pool_code": pool_code, "count": len(items), "items": items}


@router.post("/pools/{pool_code}/stocks")
def add_pool_member(pool_code: str, request: StockPoolMemberCreate) -> dict:
    try:
        item = get_stock_pool_service().add_member(
            pool_code=pool_code,
            symbol=request.symbol,
            name=request.name,
            reason=request.reason,
            tags=request.tags,
            source=request.source,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"item": item}


@router.patch("/pools/{pool_code}/stocks/{symbol}")
def update_pool_member(pool_code: str, symbol: str, request: StockPoolMemberUpdate) -> dict:
    try:
        item = get_stock_pool_service().update_member(
            pool_code=pool_code,
            symbol=symbol,
            name=request.name,
            reason=request.reason,
            tags=request.tags,
            enabled=request.enabled,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"item": item}


@router.delete("/pools/{pool_code}/stocks/{symbol}")
def remove_pool_member(pool_code: str, symbol: str) -> dict:
    try:
        item = get_stock_pool_service().remove_member(pool_code, symbol)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"removed": item}


@router.post("/pools/{pool_code}/klines/sync")
def sync_pool_klines(pool_code: str, request: KlinePoolSyncRequest) -> dict:
    try:
        return get_kline_service().sync_pool_klines(
            pool_code=pool_code,
            periods=request.periods,
            limit_symbols=request.limit_symbols,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"同步股票池 K 线失败，外部行情数据源暂不可用：{exc}") from exc


@router.post("/stocks/{symbol}/klines/sync")
def sync_symbol_kline(symbol: str, request: KlineSymbolSyncRequest) -> dict:
    try:
        return get_kline_service().sync_symbol_kline(symbol=symbol, period=request.period)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"同步单只股票 K 线失败，外部行情数据源暂不可用：{exc}") from exc


@router.get("/stocks/{symbol}/klines/local")
def list_local_klines(
    symbol: str,
    period: str = "daily",
    page: int | None = Query(default=None, ge=1, description="页码，从 1 开始"),
    page_size: int | None = Query(default=None, ge=1, le=3000, description="每页条数，日 K 建议不超过 2000"),
    limit: int | None = Query(default=None, ge=1, le=3000, description="兼容旧参数，等同 page_size"),
) -> dict:
    params = resolve_page_params(page, page_size, limit, default_page_size=800)
    service = get_kline_service()
    result = service.list_klines_page(symbol=symbol, period=period, page_params=params)
    data = result.to_dict()
    data["symbol"] = symbol.upper()
    data["period"] = period
    data["source"] = "local_db"
    data["cache_hit"] = False
    data["cache_enabled"] = service.kline_cache.enabled
    return data


@router.get("/klines/summary")
def kline_summary(
    page: int | None = Query(default=None, ge=1, description="页码，从 1 开始"),
    page_size: int | None = Query(default=None, ge=1, le=5000, description="每页条数"),
    limit: int | None = Query(default=None, ge=1, le=5000, description="兼容旧参数，等同 page_size"),
) -> dict:
    params = resolve_page_params(page, page_size, limit, default_page_size=50)
    result = get_kline_service().get_kline_summary_page(page_params=params)
    return result.to_dict()


@router.post("/pools/{pool_code}/features/compute")
def compute_pool_features(pool_code: str, request: FeaturePoolComputeRequest) -> dict:
    return get_feature_service().compute_pool_features(
        pool_code=pool_code,
        period=request.period,
        limit_symbols=request.limit_symbols,
    )


@router.post("/stocks/{symbol}/features/compute")
def compute_symbol_features(symbol: str, request: FeatureSymbolComputeRequest) -> dict:
    return get_feature_service().compute_symbol_features(symbol=symbol, period=request.period)


@router.get("/stocks/{symbol}/features/latest")
def get_latest_feature(symbol: str, period: str = "daily") -> dict:
    item = get_feature_service().get_latest_feature(symbol=symbol, period=period)
    if item is None:
        raise HTTPException(status_code=404, detail="未找到特征数据，请先同步 K 线并计算特征。")
    return {"item": item}


@router.get("/stocks/{symbol}/features")
def list_features(
    symbol: str,
    period: str = "daily",
    page: int | None = Query(default=None, ge=1, description="页码，从 1 开始"),
    page_size: int | None = Query(default=None, ge=1, le=5000, description="每页条数"),
    limit: int | None = Query(default=None, ge=1, le=5000, description="兼容旧参数，等同 page_size"),
) -> dict:
    params = resolve_page_params(page, page_size, limit, default_page_size=120)
    result = get_feature_service().list_features_page(symbol=symbol, period=period, page_params=params)
    data = result.to_dict()
    data["symbol"] = symbol.upper()
    data["period"] = period
    return data


@router.get("/data/readiness")
def check_data_readiness(
    symbol: str | None = Query(default=None, description="单只股票代码；与 pool_code 二选一"),
    pool_code: str | None = Query(default=None, description="股票池代码；与 symbol 二选一"),
    period: str = Query(default="daily", description="K线周期，默认 daily"),
    limit_symbols: int | None = Query(default=None, ge=1, le=500, description="股票池诊断时限制股票数量"),
) -> dict:
    if bool(symbol) == bool(pool_code):
        raise HTTPException(status_code=400, detail="symbol 和 pool_code 必须且只能传一个。")
    try:
        if symbol:
            return get_data_readiness_service().check_symbol(symbol=symbol, period=period)
        return get_data_readiness_service().check_pool(pool_code=pool_code or "", period=period, limit_symbols=limit_symbols)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/trading/open-position")
def open_position(request: OpenPositionRequest) -> dict:
    return get_trading_service().open_position(request)


@router.post("/trading/close-position")
def close_position(request: ClosePositionRequest) -> dict:
    return get_trading_service().close_position(request)


@router.get("/trading/positions")
def list_positions() -> dict:
    return get_trading_service().list_positions()


@router.get("/alerts/todos")
def list_alert_todos(limit_ai_records: int = Query(default=100, ge=1, le=300)) -> dict:
    return get_alert_todo_service().list_todos(limit_ai_records=limit_ai_records)


@router.patch("/alerts/todos/{todo_id}")
def update_alert_todo(todo_id: str, request: AlertTodoUpdateRequest) -> dict:
    try:
        return get_alert_todo_service().update_todo(
            todo_id,
            status=request.status,
            note=request.note,
            snooze_until=request.snooze_until,
            linked_order_id=request.linked_order_id,
        )
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if "不存在" in message else 400
        raise HTTPException(status_code=status_code, detail=message) from exc


@router.get("/trading/pnl")
def get_positions_pnl() -> dict:
    return get_trading_service().get_positions_pnl()


@router.get("/trading/pnl-stats")
def get_pnl_stats(
    strategy_mode: str | None = Query(default=None, description="按策略模式筛选：manual/strict/normal/loose；manual 表示手动交易"),
    start_date: str | None = Query(default=None, description="起始日期，格式 YYYY-MM-DD"),
    end_date: str | None = Query(default=None, description="结束日期，格式 YYYY-MM-DD"),
) -> dict:
    return get_trading_service().get_pnl_stats(
        strategy_mode=strategy_mode,
        start_date=start_date,
        end_date=end_date,
    )


@router.get("/trading/strategy-evaluation")
def strategy_evaluation(
    start_date: str | None = Query(default=None, description="起始日期，格式 YYYY-MM-DD"),
    end_date: str | None = Query(default=None, description="结束日期，格式 YYYY-MM-DD"),
) -> dict:
    return get_trading_service().get_strategy_evaluation(
        start_date=start_date,
        end_date=end_date,
    )


@router.get("/trading/daily-report")
def daily_report(
    date: str | None = Query(default=None, description="日报日期，格式 YYYY-MM-DD，默认当天"),
) -> dict:
    return get_trading_service().get_daily_report(date=date)


@router.get("/trading/account")
def paper_account() -> dict:
    return get_trading_service().account_summary()


@router.get("/trading/orders")
def list_orders(
    symbol: str | None = Query(default=None, description="按股票代码筛选"),
    side: str | None = Query(default=None, description="按方向筛选：buy/sell"),
    status: str | None = Query(default=None, description="按状态筛选：filled/rejected"),
    strategy_mode: str | None = Query(default=None, description="按策略模式筛选：manual/strict/normal/loose；manual 表示手动交易"),
    start_date: str | None = Query(default=None, description="起始日期，格式 YYYY-MM-DD"),
    end_date: str | None = Query(default=None, description="结束日期，格式 YYYY-MM-DD"),
    page: int | None = Query(default=None, ge=1, description="页码，从 1 开始"),
    page_size: int | None = Query(default=None, ge=1, le=5000, description="每页条数"),
    limit: int | None = Query(default=None, ge=1, le=5000, description="兼容旧参数，等同 page_size"),
) -> dict:
    params = resolve_page_params(page, page_size, limit, default_page_size=50)
    return get_trading_service().list_orders_page(
        page_params=params,
        symbol=symbol,
        side=side,
        status=status,
        strategy_mode=strategy_mode,
        start_date=start_date,
        end_date=end_date,
    )


@router.get("/trading/cash-flows")
def list_cash_flows(
    page: int | None = Query(default=None, ge=1, description="页码，从 1 开始"),
    page_size: int | None = Query(default=None, ge=1, le=5000, description="每页条数"),
    limit: int | None = Query(default=None, ge=1, le=5000, description="兼容旧参数，等同 page_size"),
) -> dict:
    params = resolve_page_params(page, page_size, limit, default_page_size=50)
    return get_trading_service().list_cash_flows_page(page_params=params)


@router.post("/trading/reset")
def reset_paper_account() -> dict:
    return get_trading_service().reset_paper_account()


@router.post("/trading/auto-buy")
def auto_buy(request: AutoBuyRequest) -> dict:
    return get_trading_service().run_opening_auto_buy(
        pools=request.pools,
        limit_symbols=request.limit_symbols,
        strategy_mode=request.strategy_mode,
    )


@router.post("/trading/auto-close")
def auto_close(request: AutoCloseRequest) -> dict:
    mode = request.mode
    if request.scheduled is not None:
        mode = "force_close_all" if request.scheduled else "risk_scan"
    return get_trading_service().run_scheduled_auto_close(
        strategy_mode=request.strategy_mode,
        mode=mode,
        dry_run=request.dry_run,
    )


@router.post("/backtest/run")
def run_backtest(request: BacktestRequest) -> dict:
    if bool(request.symbol) == bool(request.pool_code):
        raise HTTPException(status_code=400, detail="symbol 和 pool_code 必须且只能传一个。")
    if request.symbol:
        return get_backtest_service().run_symbol_backtest(
            symbol=request.symbol,
            period=request.period,
            strategy_mode=request.strategy_mode,
            initial_cash=request.initial_cash,
            quantity=request.quantity,
            start_date=request.start_date,
            end_date=request.end_date,
        )
    return get_backtest_service().run_pool_backtest(
        pool_code=request.pool_code or "",
        period=request.period,
        strategy_mode=request.strategy_mode,
        initial_cash=request.initial_cash,
        quantity=request.quantity,
        start_date=request.start_date,
        end_date=request.end_date,
        limit_symbols=request.limit_symbols,
    )


@router.post("/backtest/grid-optimize")
def optimize_backtest_grid(request: BacktestGridOptimizeRequest) -> dict:
    if bool(request.symbol) == bool(request.pool_code):
        raise HTTPException(status_code=400, detail="symbol 和 pool_code 必须且只能传一个。")
    try:
        return get_backtest_service().run_grid_optimization(
            symbol=request.symbol,
            pool_code=request.pool_code,
            period=request.period,
            strategy_mode=request.strategy_mode,
            initial_cash=request.initial_cash,
            quantity=request.quantity,
            start_date=request.start_date,
            end_date=request.end_date,
            limit_symbols=request.limit_symbols,
            take_profit_pct=request.take_profit_pct,
            stop_loss_pct=request.stop_loss_pct,
            min_trend_score=request.min_trend_score,
            min_confidence=request.min_confidence,
            max_combinations=request.max_combinations,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/backtest/strategy-compare")
def compare_backtest_strategies(request: BacktestStrategyCompareRequest) -> dict:
    if bool(request.symbol) == bool(request.pool_code):
        raise HTTPException(status_code=400, detail="symbol 和 pool_code 必须且只能传一个。")
    try:
        return get_backtest_service().run_strategy_comparison(
            symbol=request.symbol,
            pool_code=request.pool_code,
            period=request.period,
            strategy_modes=request.strategy_modes,
            initial_cash=request.initial_cash,
            quantity=request.quantity,
            start_date=request.start_date,
            end_date=request.end_date,
            limit_symbols=request.limit_symbols,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/backtest/runs")
def list_backtest_runs(
    scope: str | None = Query(default=None, description="回测范围：symbol/pool"),
    symbol: str | None = Query(default=None, description="按股票代码筛选"),
    pool_code: str | None = Query(default=None, description="按股票池筛选"),
    strategy_mode: str | None = Query(default=None, description="按回测策略模式筛选：strict/normal/loose"),
    status: str | None = Query(default=None, description="按回测状态筛选：ok/insufficient_data"),
    page: int | None = Query(default=None, ge=1, description="页码，从 1 开始"),
    page_size: int | None = Query(default=None, ge=1, le=5000, description="每页条数"),
    limit: int | None = Query(default=None, ge=1, le=5000, description="兼容旧参数，等同 page_size"),
) -> dict:
    params = resolve_page_params(page, page_size, limit, default_page_size=50)
    return get_backtest_service().list_runs_page(
        page_params=params,
        scope=scope,
        symbol=symbol,
        pool_code=pool_code,
        strategy_mode=strategy_mode,
        status=status,
    ).to_dict()


@router.get("/backtest/compare")
def compare_backtest_runs(
    scope: str | None = Query(default=None, description="回测范围：symbol/pool"),
    symbol: str | None = Query(default=None, description="按股票代码筛选"),
    pool_code: str | None = Query(default=None, description="按股票池筛选"),
    strategy_mode: str | None = Query(default=None, description="按回测策略模式筛选：strict/normal/loose"),
    status: str | None = Query(default="ok", description="按回测状态筛选，默认 ok；传空值可比较全部"),
    sort_by: str = Query(default="score", description="排序字段：score/total_pnl_pct/max_drawdown/win_rate/trade_count/risk_return_ratio"),
    sort_order: str = Query(default="desc", description="排序方向：desc/asc"),
    limit: int = Query(default=20, ge=1, le=200, description="返回条数"),
) -> dict:
    return get_backtest_service().compare_runs(
        scope=scope,
        symbol=symbol,
        pool_code=pool_code,
        strategy_mode=strategy_mode,
        status=status,
        sort_by=sort_by,
        sort_order=sort_order,
        limit=limit,
    )


@router.get("/backtest/leaderboard")
def backtest_leaderboard(
    scope: str | None = Query(default=None, description="回测范围：symbol/pool"),
    limit: int = Query(default=20, ge=1, le=200, description="返回条数"),
) -> dict:
    return get_backtest_service().compare_runs(scope=scope, status="ok", sort_by="score", sort_order="desc", limit=limit)


@router.get("/backtest/runs/{run_id}/report")
def get_backtest_report(
    run_id: str,
    include_trades: bool = Query(default=True, description="是否在报告中分析成交明细，默认 true"),
    top_trades: int = Query(default=10, ge=1, le=100, description="报告中最多分析的成交条数"),
) -> dict:
    try:
        return get_backtest_service().get_run_report(run_id, include_trades=include_trades, top_trades=top_trades)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/backtest/runs/{run_id}/report/export")
def export_backtest_report(
    run_id: str,
    format: str = Query(default="markdown", description="导出格式：markdown 或 html"),
    include_trades: bool = Query(default=True, description="是否在报告中分析成交明细，默认 true"),
    top_trades: int = Query(default=10, ge=1, le=100, description="报告中最多分析的成交条数"),
) -> Response:
    try:
        content, media_type = get_backtest_service().export_run_report(
            run_id, fmt=format, include_trades=include_trades, top_trades=top_trades,
        )
        return Response(content=content, media_type=media_type)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/backtest/runs/{run_id}")
def get_backtest_run(
    run_id: str,
    include_trades: bool = Query(default=True, description="是否返回成交明细，默认 true"),
    include_equity: bool = Query(default=False, description="是否返回权益曲线，默认 false，避免大响应"),
    trades_page: int | None = Query(default=None, ge=1, description="成交明细页码，从 1 开始"),
    trades_page_size: int | None = Query(default=None, ge=1, le=500, description="成交明细每页条数，默认 200"),
    equity_page: int | None = Query(default=None, ge=1, description="权益曲线页码，从 1 开始"),
    equity_page_size: int | None = Query(default=None, ge=1, le=500, description="权益曲线每页条数，默认 200"),
    equity_stride: int = Query(default=1, ge=1, le=100, description="权益曲线采样步长，1 表示不采样"),
) -> dict:
    try:
        return get_backtest_service().get_run_detail(
            run_id,
            include_trades=include_trades,
            include_equity=include_equity,
            trades_page_params=resolve_page_params(trades_page, trades_page_size, None, default_page_size=200),
            equity_page_params=resolve_page_params(equity_page, equity_page_size, None, default_page_size=200),
            equity_stride=equity_stride,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/backtest/cache/status")
def get_backtest_cache_status() -> dict:
    """查看回测缓存状态和统计信息。"""
    return get_backtest_service().cache.status()


@router.post("/backtest/cache/invalidate")
def invalidate_backtest_cache(
    symbol: str | None = Query(default=None, description="按股票代码清除缓存"),
    pool_code: str | None = Query(default=None, description="按股票池清除缓存"),
    all: bool = Query(default=False, description="清除所有回测缓存"),
) -> dict:
    """清除回测缓存。可按股票代码、股票池或全部清除。"""
    cache = get_backtest_service().cache
    if all:
        deleted = cache.invalidate_all()
        return {"cleared": "all", "deleted_keys": deleted}
    if symbol:
        deleted = cache.invalidate_symbol(symbol)
        return {"cleared": "symbol", "symbol": symbol.upper(), "deleted_keys": deleted}
    if pool_code:
        deleted = cache.invalidate_pool(pool_code)
        return {"cleared": "pool", "pool_code": pool_code, "deleted_keys": deleted}
    raise HTTPException(status_code=400, detail="请指定 symbol、pool_code 或 all=true")


@router.get("/system/tasks/executions")
def list_task_executions(
    task_name: str | None = Query(default=None, description="按任务名称筛选"),
    task_type: str | None = Query(default=None, description="按任务类型筛选：auto_trade/data_sync/feature_compute"),
    status: str | None = Query(default=None, description="按状态筛选：running/success/failed"),
    trigger_type: str | None = Query(default=None, description="按触发方式筛选：manual_api/scheduler"),
    page: int | None = Query(default=None, ge=1, description="页码，从 1 开始"),
    page_size: int | None = Query(default=None, ge=1, le=5000, description="每页条数"),
    limit: int | None = Query(default=None, ge=1, le=5000, description="兼容旧参数，等同 page_size"),
) -> dict:
    params = resolve_page_params(page, page_size, limit, default_page_size=50)
    return get_task_execution_service().list_executions_page(
        page_params=params,
        task_name=task_name,
        task_type=task_type,
        status=status,
        trigger_type=trigger_type,
    ).to_dict()


@router.get("/system/tasks/executions/latest")
def latest_task_executions(limit_per_task: int = Query(default=1, ge=1, le=20, description="每个任务返回最近 N 条")) -> dict:
    return get_task_execution_service().latest_executions(limit_per_task=limit_per_task)
