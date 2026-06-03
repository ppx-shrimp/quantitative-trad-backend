from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any, Callable, NotRequired, TypedDict, cast

from quant_system.ai.llm_client import LLMClientError, OpenAICompatibleClient
from quant_system.ai.prompts import build_stock_analysis_prompt
from quant_system.ai.schemas import AIStockAnalysisRequest, AIStockDecision, AIStockAnalysisResponse
from quant_system.core.config import settings
from quant_system.services.analysis_service import AnalysisService
from quant_system.services.feature_service import FeatureService
from quant_system.services.kline_service import KlineService
from quant_system.services.news_service import NewsService
from quant_system.services.trading_service import TradingService


class StockAnalysisState(TypedDict):
    analysis_id: str
    symbol: str
    request: AIStockAnalysisRequest
    created_at: str
    context: dict[str, Any]
    precheck: dict[str, Any]
    prompt: str | None
    raw_output: dict[str, Any] | None
    decision: AIStockDecision | None
    status: str
    error_message: str | None
    provider: str
    model: str
    prompt_version: str
    trace: list[dict[str, Any]]
    current_stage: str | None
    workflow_started_at: float
    stage_timings: list[dict[str, Any]]
    services: NotRequired[dict[str, Any]]
    llm_client: NotRequired[OpenAICompatibleClient]


class StockAnalysisWorkflow:
    def __init__(self, services: dict[str, Any], llm_client: OpenAICompatibleClient) -> None:
        self.services = services
        self.llm_client = llm_client
        self._compiled_graph = self._try_compile_langgraph()

    def run(self, *, analysis_id: str, symbol: str, request: AIStockAnalysisRequest, created_at: str) -> AIStockAnalysisResponse:
        initial_state: StockAnalysisState = {
            "analysis_id": analysis_id,
            "symbol": symbol,
            "request": request,
            "created_at": created_at,
            "context": {},
            "precheck": {},
            "prompt": None,
            "raw_output": None,
            "decision": None,
            "status": "success",
            "error_message": None,
            "provider": settings.llm_provider if self.llm_client.enabled() else "mock",
            "model": settings.llm_model if self.llm_client.enabled() else "rule_mock_v1",
            "prompt_version": settings.ai_prompt_version,
            "trace": [],
            "current_stage": None,
            "workflow_started_at": time.perf_counter(),
            "stage_timings": [],
            "services": self.services,
            "llm_client": self.llm_client,
        }
        try:
            if self._compiled_graph is not None:
                final_state = self._compiled_graph.invoke(initial_state)
            else:
                final_state = self._run_fallback_graph(initial_state)
        except Exception as exc:
            final_state = initial_state
            final_state["status"] = "failed"
            final_state["error_message"] = str(exc)
            final_state["raw_output"] = {"error": str(exc)}
            stage = final_state.get("current_stage") or "workflow"
            self._record_stage_timing(final_state, stage, "failed", error=str(exc))
            final_state["current_stage"] = None
            self._append_trace(final_state, "workflow_error", "failed", str(exc))
        workflow_profile = self._workflow_profile(final_state)
        return AIStockAnalysisResponse(
            analysis_id=final_state["analysis_id"],
            symbol=final_state["symbol"],
            analysis_type=final_state["request"].analysis_type,
            status=final_state["status"],
            provider=final_state["provider"],
            model=final_state["model"],
            prompt_version=final_state["prompt_version"],
            decision=final_state["decision"],
            context={**final_state["context"], "workflow_trace": final_state["trace"], "workflow_profile": workflow_profile, "precheck": final_state["precheck"]},
            raw_output=final_state["raw_output"],
            error_message=final_state["error_message"],
            created_at=final_state["created_at"],
        )

    def _try_compile_langgraph(self):
        try:
            from langgraph.graph import END, StateGraph
        except Exception:
            return None

        graph = StateGraph(StockAnalysisState)
        graph.add_node("collect_context", lambda state: self._run_timed_stage(state, code="collect_context", label="收集上下文", fn=self._collect_context_node))
        graph.add_node("rule_precheck", lambda state: self._run_timed_stage(state, code="rule_precheck", label="规则预检", fn=self._rule_precheck_node))
        graph.add_node("build_prompt", lambda state: self._run_timed_stage(state, code="build_prompt", label="构建 Prompt", fn=self._build_prompt_node))
        graph.add_node("llm_or_mock", lambda state: self._run_timed_stage(state, code="llm_or_mock", label="调用 LLM / Mock", fn=self._llm_or_mock_node))
        graph.add_node("validate_decision", lambda state: self._run_timed_stage(state, code="validate_decision", label="校验 AI 决策", fn=self._validate_decision_node))
        graph.add_node("finalize", lambda state: self._run_timed_stage(state, code="finalize", label="完成落库前整理", fn=self._finalize_node))
        graph.set_entry_point("collect_context")
        graph.add_edge("collect_context", "rule_precheck")
        graph.add_edge("rule_precheck", "build_prompt")
        graph.add_edge("build_prompt", "llm_or_mock")
        graph.add_edge("llm_or_mock", "validate_decision")
        graph.add_edge("validate_decision", "finalize")
        graph.add_edge("finalize", END)
        return graph.compile()

    def _run_fallback_graph(self, state: StockAnalysisState) -> StockAnalysisState:
        for code, label, node in [
            ("collect_context", "收集上下文", self._collect_context_node),
            ("rule_precheck", "规则预检", self._rule_precheck_node),
            ("build_prompt", "构建 Prompt", self._build_prompt_node),
            ("llm_or_mock", "调用 LLM / Mock", self._llm_or_mock_node),
            ("validate_decision", "校验 AI 决策", self._validate_decision_node),
            ("finalize", "完成落库前整理", self._finalize_node),
        ]:
            state = self._run_timed_stage(state, code=code, label=label, fn=node)
        return state

    def _run_timed_stage(
        self,
        state: StockAnalysisState,
        *,
        code: str,
        label: str,
        fn: Callable[[StockAnalysisState], StockAnalysisState],
    ) -> StockAnalysisState:
        state["current_stage"] = code
        started = time.perf_counter()
        try:
            next_state = fn(state)
        except Exception as exc:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            state["stage_timings"].append({
                "code": code,
                "label": label,
                "status": "failed",
                "duration_ms": duration_ms,
                "error": str(exc),
            })
            state["current_stage"] = code
            raise
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        next_state["stage_timings"].append({
            "code": code,
            "label": label,
            "status": "success",
            "duration_ms": duration_ms,
            "error": None,
        })
        next_state["current_stage"] = None
        return next_state

    def _record_stage_timing(self, state: StockAnalysisState, code: str, status: str, *, error: str | None = None) -> None:
        existing_failed = any(item.get("code") == code and item.get("status") == "failed" for item in state.get("stage_timings") or [])
        if existing_failed:
            return
        state["stage_timings"].append({
            "code": code,
            "label": code,
            "status": status,
            "duration_ms": 0,
            "error": error,
        })

    def _workflow_profile(self, state: StockAnalysisState) -> dict[str, Any]:
        total_ms = round((time.perf_counter() - state.get("workflow_started_at", time.perf_counter())) * 1000, 2)
        stages = state.get("stage_timings") or []
        failed = next((item for item in stages if item.get("status") == "failed"), None)
        return {
            "total_duration_ms": total_ms,
            "stages": stages,
            "failed_stage": failed,
            "failed_reason": failed.get("error") if failed else state.get("error_message"),
            "status": state.get("status"),
        }

    def _collect_context_node(self, state: StockAnalysisState) -> StockAnalysisState:
        request = state["request"]
        symbol = state["symbol"]
        analysis_service: AnalysisService = state["services"]["analysis_service"]
        feature_service: FeatureService = state["services"]["feature_service"]
        kline_service: KlineService = state["services"]["kline_service"]
        news_service: NewsService = state["services"]["news_service"]
        trading_service: TradingService = state["services"]["trading_service"]
        news_embedding_service = state["services"].get("news_embedding_service")
        evaluation_service = state["services"].get("evaluation_service")

        snapshot = self._safe_call(lambda: analysis_service.market_data.get_snapshot(symbol), None)
        technical = self._safe_call(lambda: feature_service.analyze_symbol(symbol, period="daily"), {"status": "failed", "summary": "技术特征不可用"})
        klines = self._safe_call(lambda: kline_service.list_klines(symbol, period="daily", limit=settings.ai_context_kline_limit), [])
        news = []
        rag_news_context = self._empty_rag_news_context(enabled=False, query=None, error="本次分析未启用资讯上下文。")
        if request.include_news:
            news_result = self._safe_call(lambda: news_service.list_symbol_news(symbol, page_params=self._page_params(), news_type=None).to_dict(), {"items": []})
            news = (news_result or {}).get("items", [])[: settings.ai_context_news_limit]
            rag_news_context = self._build_rag_news_context(
                service=news_embedding_service,
                symbol=symbol,
                request=request,
                technical=technical,
                snapshot=self._snapshot_to_dict(snapshot),
            )
            if rag_news_context.get("ok"):
                self._append_trace(state, "rag_news_context", "success", f"已检索 RAG 新闻证据 {rag_news_context.get('count') or 0} 条")
            else:
                self._append_trace(state, "rag_news_context", "skipped", str(rag_news_context.get("error") or "RAG 新闻证据不可用"))
        positions_payload = self._safe_call(lambda: trading_service.get_positions_pnl(), {"positions": [], "summary": {}})
        position = None
        if request.include_position:
            position = next((item for item in positions_payload.get("positions", []) if self._normalize_symbol(item.get("symbol")) == symbol), None)
        snapshot_dict = self._snapshot_to_dict(snapshot)
        recent_klines = klines[-settings.ai_context_kline_limit :] if isinstance(klines, list) else []
        evaluation_feedback = self._safe_call(
            lambda: evaluation_service.feedback_for_prompt(symbol=symbol, limit=50) if evaluation_service else {"available": False},
            {"available": False, "error": "复盘反哺读取失败"},
        )
        context = {
            "symbol": symbol,
            "analysis_type": request.analysis_type,
            "horizon": request.horizon,
            "snapshot": snapshot_dict,
            "technical": technical,
            "recent_klines": recent_klines,
            "news": news,
            "rag_news_context": rag_news_context,
            "position": position,
            "account_summary": positions_payload.get("summary", {}),
            "risk_rules": self._risk_rules(),
            "evaluation_feedback": evaluation_feedback,
            "llm_mode": "real" if state["llm_client"].enabled() else "mock",
        }
        context["analysis_context_summary"] = self._build_context_summary(context)
        state["context"] = context
        self._append_trace(state, "collect_context", "success", "已收集并摘要化行情、K线、资讯、持仓和风控规则上下文")
        return state

    def _rule_precheck_node(self, state: StockAnalysisState) -> StockAnalysisState:
        context = state["context"]
        technical = context.get("technical") or {}
        scores = technical.get("scores") or {}
        latest = technical.get("latest_feature") or {}
        position = context.get("position") or {}
        trend_score = self._to_float(scores.get("trend") or latest.get("trend_score"), 50.0)
        technical_score = self._to_float(scores.get("technical"), 50.0)
        risk_score = self._to_float(scores.get("risk"), 50.0)
        pnl_pct = self._to_float(position.get("unrealized_pnl_pct"), 0.0) if position else None
        precheck = {
            "trend_score": trend_score,
            "technical_score": technical_score,
            "risk_score": risk_score,
            "signal": str(latest.get("signal") or "neutral"),
            "position_pnl_pct": pnl_pct,
            "risk_trigger": self._risk_trigger_from_pnl(pnl_pct),
            "data_quality": {
                "has_snapshot": bool(context.get("snapshot")),
                "kline_count": len(context.get("recent_klines") or []),
                "news_count": len(context.get("news") or []),
                "rag_news_count": self._rag_news_count(context.get("rag_news_context")),
                "has_position": bool(position),
            },
        }
        state["precheck"] = precheck
        self._append_trace(state, "rule_precheck", "success", "已完成技术评分、持仓盈亏和数据质量预判")
        return state

    def _build_prompt_node(self, state: StockAnalysisState) -> StockAnalysisState:
        prompt = build_stock_analysis_prompt({**state["context"], "precheck": state["precheck"]}, state["request"].user_question)
        original_chars = len(prompt)
        max_chars = max(8000, int(settings.ai_max_prompt_chars or 24000))
        if original_chars > max_chars:
            prompt = prompt[:max_chars].rstrip() + "\n...[Prompt 已按成本预算截断，请优先基于已保留的结构化摘要输出保守结论]"
            cost_guard = dict((state["context"].get("cost_guard") or {}))
            cost_guard.update({"prompt_trimmed": True, "original_prompt_chars": original_chars, "final_prompt_chars": len(prompt), "max_prompt_chars": max_chars})
            state["context"]["cost_guard"] = cost_guard
        else:
            cost_guard = dict((state["context"].get("cost_guard") or {}))
            cost_guard.update({"prompt_trimmed": False, "original_prompt_chars": original_chars, "final_prompt_chars": len(prompt), "max_prompt_chars": max_chars})
            state["context"]["cost_guard"] = cost_guard
        state["prompt"] = prompt
        self._append_trace(state, "build_prompt", "success", f"prompt_version={settings.ai_prompt_version}, prompt_chars={len(prompt)}")
        return state

    def _llm_or_mock_node(self, state: StockAnalysisState) -> StockAnalysisState:
        llm_client = state["llm_client"]
        analysis_type = state["request"].analysis_type
        if llm_client.enabled():
            if not state["prompt"]:
                raise LLMClientError("Prompt 未生成，无法调用 LLM。")
            try:
                state["raw_output"] = llm_client.complete_json(state["prompt"], analysis_type=analysis_type)
                self._append_trace(state, "llm_or_mock", "success", f"已调用真实模型 {settings.llm_model}，analysis_type={analysis_type}")
            except Exception as exc:
                state["raw_output"] = self._mock_decision(state)
                fallback = state["raw_output"].setdefault("data_quality", {})
                fallback["llm_fallback"] = True
                fallback["llm_error"] = str(exc)
                self._append_trace(state, "llm_or_mock", "fallback", f"真实模型调用失败，已降级为规则 mock：{exc}")
        else:
            state["raw_output"] = self._mock_decision(state)
            self._append_trace(state, "llm_or_mock", "success", "未配置真实模型，已使用规则 mock 决策")
        return state

    def _validate_decision_node(self, state: StockAnalysisState) -> StockAnalysisState:
        raw_output = state.get("raw_output") or {}
        try:
            decision = AIStockDecision.model_validate(raw_output)
            decision = self._apply_hard_risk_constraints(decision, state)
            decision = self._normalize_execution_plan(decision, state)
            decision = self._apply_output_quality_guard(decision, state)
            state["decision"] = decision
            self._append_trace(state, "validate_decision", "success", "AI 输出结构、风控硬约束、执行计划纪律和质量保护校验通过")
        except Exception as exc:
            raise LLMClientError(f"AI 决策输出结构不符合要求：{exc}") from exc
        return state

    def _apply_hard_risk_constraints(self, decision: AIStockDecision, state: StockAnalysisState) -> AIStockDecision:
        """用系统风控规则强制约束模型输出。

        任何真实模型或 mock 输出都不能覆盖用户固定风控纪律：
        - 浮亏 <= -10% 或浮盈 >= 50%：最终 action 必须为 sell
        - 浮亏 <= -5%、浮盈 >= 10% / 20%：最终 action 必须为 reduce
        """
        precheck = state.get("precheck") or {}
        risk_trigger = precheck.get("risk_trigger") or {}
        forced_action = str(risk_trigger.get("action") or "")
        if forced_action not in {"sell", "reduce"}:
            return decision

        original_action = decision.action
        original_confidence = decision.confidence
        trigger_message = str(risk_trigger.get("message") or "已触发系统风控规则。")
        trigger_confidence = self._to_float(risk_trigger.get("confidence"), 0.75)
        hard_warnings: list[str] = []

        if original_action != forced_action:
            decision.action = cast(Any, forced_action)
            hard_warnings.append(f"模型原始建议 {original_action} 与风控硬约束冲突，最终 action 已强制改为 {forced_action}")
            self._append_trace(
                state,
                "hard_risk_constraint",
                "overridden",
                f"risk_trigger={forced_action}, original_action={original_action}, final_action={forced_action}",
            )
        else:
            self._append_trace(state, "hard_risk_constraint", "passed", f"action={forced_action} 符合已触发风控规则")

        decision.risk_level = "high"
        decision.confidence = max(original_confidence, trigger_confidence)
        if trigger_message not in decision.risk_warnings:
            decision.risk_warnings.append(trigger_message)
        if hard_warnings:
            decision.risk_warnings.extend(hard_warnings)

        data_quality = dict(decision.data_quality or {})
        existing = data_quality.get("hard_risk_constraints") or []
        if not isinstance(existing, list):
            existing = [str(existing)]
        existing.append({
            "forced_action": forced_action,
            "original_action": original_action,
            "trigger_message": trigger_message,
            "original_confidence": round(original_confidence, 4),
            "final_confidence": round(decision.confidence, 4),
        })
        data_quality["hard_risk_constraints"] = existing
        if hard_warnings:
            plan_warnings = data_quality.get("plan_quality_warnings") or []
            if not isinstance(plan_warnings, list):
                plan_warnings = [str(plan_warnings)]
            data_quality["plan_quality_warnings"] = plan_warnings + hard_warnings
        decision.data_quality = data_quality
        return decision

    def _normalize_execution_plan(self, decision: AIStockDecision, state: StockAnalysisState) -> AIStockDecision:
        plan = dict(decision.suggested_plan or {})
        context = state.get("context") or {}
        precheck = state.get("precheck") or {}
        summary = context.get("analysis_context_summary") or {}
        technical_summary = summary.get("technical_summary") or {}
        risk_trigger = precheck.get("risk_trigger") or {}
        warnings: list[str] = []

        fallback_plan = self._fallback_execution_plan(decision.action, state)
        required_fields = [
            "execution",
            "position_size",
            "entry_condition",
            "watch_condition",
            "stop_loss",
            "take_profit",
            "invalid_condition",
            "review_time",
            "next_step",
        ]
        for key in required_fields:
            if not self._is_specific_plan_text(plan.get(key)):
                plan[key] = fallback_plan[key]
                warnings.append(f"{key} 缺少可验证条件，已使用系统纪律模板补齐")

        if decision.action == "buy":
            if not self._is_specific_plan_text(plan.get("entry_condition")):
                plan["entry_condition"] = fallback_plan["entry_condition"]
            if not self._is_specific_plan_text(plan.get("stop_loss")):
                plan["stop_loss"] = fallback_plan["stop_loss"]
            if decision.confidence > 0.65 and (not plan.get("entry_condition") or not plan.get("stop_loss")):
                decision.confidence = 0.65
                warnings.append("buy 建议缺少入场或止损纪律，置信度按 v3 上限降至 0.65")

        if risk_trigger.get("action"):
            trigger_action = str(risk_trigger.get("action"))
            if trigger_action in {"sell", "reduce"} and decision.action != trigger_action:
                decision.action = cast(Any, trigger_action)
                warnings.append("已触发系统风控规则，执行计划阶段再次强制校正 action")
            plan["execution"] = "已触发系统风控硬约束，禁止给出相反交易建议；仅允许由用户在交易面板手动确认减仓/卖出。"
            plan["next_step"] = str(risk_trigger.get("message") or fallback_plan["next_step"])
            plan["stop_loss"] = fallback_plan["stop_loss"]
            plan["take_profit"] = fallback_plan["take_profit"]

        if technical_summary.get("ma_structure") and "均线" not in str(plan.get("watch_condition")):
            plan["watch_condition"] = f"{technical_summary['ma_structure']}；{plan['watch_condition']}"

        data_quality = dict(decision.data_quality or {})
        if warnings:
            existing = data_quality.get("plan_quality_warnings") or []
            if not isinstance(existing, list):
                existing = [str(existing)]
            data_quality["plan_quality_warnings"] = existing + warnings
            if not data_quality.get("confidence_adjustment") and any("置信度" in warning for warning in warnings):
                data_quality["confidence_adjustment"] = "；".join(warnings)

        decision.suggested_plan = plan
        decision.data_quality = data_quality
        return decision

    def _apply_output_quality_guard(self, decision: AIStockDecision, state: StockAnalysisState) -> AIStockDecision:
        """Final guardrail pass for trading discipline, evidence sufficiency, and vague output."""
        data_quality = dict(decision.data_quality or {})
        precheck_quality = ((state.get("precheck") or {}).get("data_quality") or {})
        plan = dict(decision.suggested_plan or {})
        issues: list[dict[str, Any]] = []
        adjustments: list[str] = []

        def add_issue(code: str, severity: str, message: str, field: str | None = None) -> None:
            issues.append({"code": code, "severity": severity, "message": message, "field": field})

        execution_text = " ".join(str(plan.get(key) or "") for key in ["execution", "next_step", "position_size"])
        forbidden_phrases = ["自动下单", "自动买入", "自动卖出", "无需确认", "满仓", "全仓买入", "梭哈", "重仓"]
        if any(phrase in execution_text for phrase in forbidden_phrases):
            add_issue("unsafe_trading_instruction", "blocking", "执行计划包含自动下单、满仓或绕过人工确认的表述，已改写为手动确认纪律。", "suggested_plan.execution")
            plan["execution"] = "AI 仅提供决策辅助和交易单预填建议，禁止自动下单；必须由用户在交易面板核对价格、数量和风控后手动确认。"
            plan["position_size"] = "A 股最小交易单位 100 股；默认小仓试探，禁止满仓/重仓表述。"
            plan["next_step"] = "先复核行情、K线、RAG 证据和风控线，再决定是否带入交易面板手动确认。"

        if decision.action == "buy" and not self._contains_risk_discipline(plan):
            add_issue("missing_buy_risk_discipline", "blocking", "买入建议缺少明确止损/失效纪律，已补齐并限制置信度。", "suggested_plan.stop_loss")
            fallback = self._fallback_execution_plan(decision.action, state)
            plan["stop_loss"] = fallback["stop_loss"]
            plan["invalid_condition"] = fallback["invalid_condition"]
            if decision.confidence > 0.65:
                decision.confidence = 0.65
                adjustments.append("买入建议缺少止损或失效纪律，置信度上限降至 0.65")

        kline_count = self._to_int(precheck_quality.get("kline_count") or data_quality.get("kline_count"), 0)
        news_count = self._to_int(precheck_quality.get("news_count") or data_quality.get("news_count"), 0)
        rag_news_count = self._to_int(precheck_quality.get("rag_news_count") or data_quality.get("rag_news_count"), 0)
        has_snapshot = bool(precheck_quality.get("has_snapshot", data_quality.get("has_snapshot", True)))
        evidence_issues = []
        if not has_snapshot:
            evidence_issues.append("行情快照缺失")
            decision.confidence = min(decision.confidence, 0.40)
        if kline_count <= 0:
            evidence_issues.append("K 线数据为 0")
            decision.confidence = min(decision.confidence, 0.55)
        elif kline_count < 10:
            evidence_issues.append(f"K 线仅 {kline_count} 条")
            decision.confidence = min(decision.confidence, 0.60)
        if news_count <= 0 and rag_news_count <= 0:
            evidence_issues.append("本地资讯和 RAG 新闻证据均缺失")
            if decision.action == "buy":
                decision.confidence = min(decision.confidence, 0.58)
        if evidence_issues:
            add_issue("insufficient_evidence", "warning", "；".join(evidence_issues) + "，已限制结论强度。", "data_quality")
            adjustments.append("证据不足，置信度已按数据质量上限降级")

        vague_fields = [key for key in ["entry_condition", "watch_condition", "stop_loss", "take_profit", "invalid_condition", "next_step"] if self._is_vague_output(plan.get(key))]
        if vague_fields:
            add_issue("vague_execution_plan", "warning", f"执行计划字段仍偏空泛：{', '.join(vague_fields)}，已用系统纪律模板兜底。", "suggested_plan")
            fallback = self._fallback_execution_plan(decision.action, state)
            for key in vague_fields:
                plan[key] = fallback.get(key, plan.get(key))
            decision.confidence = min(decision.confidence, 0.68)
            adjustments.append("执行计划存在空泛字段，已兜底并限制置信度")

        original_action = decision.action
        if decision.action == "buy" and (not has_snapshot or kline_count <= 0):
            decision.action = "watch"
            add_issue("buy_downgraded_by_data_quality", "blocking", "买入建议缺少行情或 K 线基础数据，已降级为观察。", "action")
            adjustments.append(f"action 从 {original_action} 降级为 watch")

        if issues:
            existing_warnings = data_quality.get("plan_quality_warnings") or []
            if not isinstance(existing_warnings, list):
                existing_warnings = [str(existing_warnings)]
            data_quality["plan_quality_warnings"] = existing_warnings + [item["message"] for item in issues]
            if adjustments:
                data_quality["confidence_adjustment"] = "；".join(dict.fromkeys(adjustments))
            for issue in issues:
                message = issue["message"]
                if message not in decision.risk_warnings:
                    decision.risk_warnings.append(message)

        blocking_count = sum(1 for item in issues if item.get("severity") == "blocking")
        warning_count = sum(1 for item in issues if item.get("severity") == "warning")
        quality_status = "blocked_adjusted" if blocking_count else ("warning_adjusted" if warning_count else "passed")
        data_quality["output_quality_guard"] = {
            "status": quality_status,
            "issues": issues,
            "blocking_count": blocking_count,
            "warning_count": warning_count,
            "adjustments": adjustments,
            "version": "output_quality_guard_v1",
        }
        decision.suggested_plan = plan
        decision.data_quality = data_quality
        return decision

    def _fallback_execution_plan(self, action: str, state: StockAnalysisState) -> dict[str, str]:
        context = state.get("context") or {}
        summary = context.get("analysis_context_summary") or {}
        technical_summary = summary.get("technical_summary") or {}
        position_summary = summary.get("position_summary") or {}
        market_snapshot = summary.get("market_snapshot") or {}
        current_price = self._to_float(market_snapshot.get("current_price") or (context.get("snapshot") or {}).get("price"), 0)
        avg_cost = self._to_float((position_summary or {}).get("avg_cost"), 0)
        price_part = f"当前价 {current_price:.2f}" if current_price > 0 else "当前价缺失"
        cost_part = f"持仓成本 {avg_cost:.2f}" if avg_cost > 0 else "无持仓成本参考"
        ma_part = str(technical_summary.get("ma_structure") or "观察 MA5/MA10/MA20 结构")
        volume_part = str(technical_summary.get("volume") or "观察成交量是否明显放大或萎缩")

        if action == "buy":
            execution = "仅在条件触发后小仓试探，默认 100 股最小单位，不自动下单。"
            next_step = "等待价格站上关键均线且量能不萎缩后，再到交易面板手动确认 100 股试探。"
        elif action in {"sell", "reduce"}:
            execution = "优先按系统风控纪律减仓/卖出，由用户在交易面板手动确认。"
            next_step = "若已触发止损止盈线，优先带入交易面板执行减仓或卖出确认。"
        elif action == "hold":
            execution = "继续持有但不加仓，按收盘价和风控线复核。"
            next_step = "继续持有至下一个交易日收盘后复盘，若触发风控线立即处理。"
        else:
            execution = "继续观察，不新开仓，不自动下单。"
            next_step = "等待趋势、量能和价格位置给出更明确同向信号后再重新分析。"

        return {
            "execution": execution,
            "position_size": "A 股最小交易单位 100 股；买入默认 100 股试探，减仓按 1/3、1/2 或全仓规则取整到 100 股。",
            "entry_condition": f"{price_part}；仅当收盘价站上关键均线、趋势评分改善且量能不明显萎缩时才考虑买入/加仓。",
            "watch_condition": f"{ma_part}；{volume_part}；同时观察资讯是否出现重大利空/利好。",
            "stop_loss": f"{cost_part}；若较成本价下跌 5% 减半仓，下跌 10% 清仓；若无持仓则以买入价为纪律参考。",
            "take_profit": f"{cost_part}；若较成本价上涨 10% 减 1/3，上涨 20% 减半，上涨 50% 清仓。",
            "invalid_condition": "若收盘价跌破关键均线、放量下跌、趋势评分继续恶化，或出现重大利空，本次判断失效。",
            "review_time": "下一个交易日收盘后复盘；若盘中触发止损/止盈/重大资讯条件则立即复核。",
            "next_step": next_step,
        }

    def _is_specific_plan_text(self, value: Any) -> bool:
        text = str(value or "").strip()
        if len(text) < 8:
            return False
        vague_phrases = ["择机", "适时", "继续观察", "注意风险", "视情况", "根据市场情况"]
        if text in vague_phrases:
            return False
        return True

    def _is_vague_output(self, value: Any) -> bool:
        text = str(value or "").strip()
        if len(text) < 12:
            return True
        vague_phrases = ["择机", "适时", "视情况", "注意风险", "根据市场情况", "合理控制", "自行判断"]
        return any(phrase == text or text.endswith(phrase) for phrase in vague_phrases)

    def _contains_risk_discipline(self, plan: dict[str, Any]) -> bool:
        text = " ".join(str(plan.get(key) or "") for key in ["stop_loss", "invalid_condition", "execution", "next_step"])
        required_markers = ["止损", "跌破", "失效", "风控", "减仓", "清仓"]
        return any(marker in text for marker in required_markers)

    def _finalize_node(self, state: StockAnalysisState) -> StockAnalysisState:
        state["status"] = "success"
        self._append_trace(state, "finalize", "success", "分析流程完成，等待落库")
        state.pop("services", None)
        state.pop("llm_client", None)
        return state

    def _mock_decision(self, state: StockAnalysisState) -> dict[str, Any]:
        request = state["request"]
        context = state["context"]
        precheck = state["precheck"]
        trend_score = self._to_float(precheck.get("trend_score"), 50.0)
        technical_score = self._to_float(precheck.get("technical_score"), 50.0)
        risk_score = self._to_float(precheck.get("risk_score"), 50.0)
        signal = str(precheck.get("signal") or "neutral")
        risk_trigger = precheck.get("risk_trigger") or {}
        technical = context.get("technical") or {}
        action = "watch"
        confidence = 0.52
        risk_level = "medium"
        reasons = [technical.get("summary") or "技术特征数据有限，采用规则 mock 分析。"]
        risk_warnings: list[str] = []
        confidence_adjustment: str | None = None

        # ── 分析类型差异化逻辑 ──
        if request.analysis_type == "risk_review":
            # 风险审查模式：侧重风险暴露
            if risk_score <= 40:
                action = "reduce"
                risk_level = "high"
                confidence = 0.72
                reasons.append("风险评分偏低，存在多维度风险暴露。")
            elif risk_score <= 55:
                action = "hold"
                risk_level = "medium"
                confidence = 0.58
                reasons.append("风险评分中等，需持续关注。")
            else:
                action = "hold"
                risk_level = "low"
                confidence = 0.65
                reasons.append("风险评分偏高（低风险），暂无明显风险信号。")
        elif request.analysis_type == "position_review":
            # 持仓复盘模式
            pnl_pct = self._to_float(precheck.get("position_pnl_pct"), 0.0) if precheck.get("position_pnl_pct") is not None else None
            if pnl_pct is None:
                action = "watch"
                confidence = 0.40
                risk_warnings.append("未检测到该股票的持仓记录，无法进行持仓复盘。")
                confidence_adjustment = "无持仓数据，置信度下调至 0.40"
            elif trend_score >= 60 and technical_score >= 55:
                action = "hold"
                confidence = min(0.75, 0.55 + (trend_score - 55) / 100)
                reasons.append("持仓趋势延续良好，技术面支撑。")
            else:
                action = "reduce"
                confidence = 0.62
                reasons.append("持仓趋势走弱或技术面不佳，建议减仓观察。")
        else:
            # 买入决策模式（默认）
            if trend_score >= 65 and technical_score >= 60 and signal in {"bullish", "buy", "strong_buy"}:
                action = "buy"
                confidence = min(0.82, 0.55 + (trend_score - 60) / 100)
                reasons.append("趋势和技术评分偏强，可进入候选观察或小仓位试探。")
            elif trend_score <= 42 or risk_score <= 40:
                action = "avoid"
                confidence = 0.68
                risk_level = "high"
                reasons.append("趋势评分或风险评分偏弱，当前不适合主动加仓。")

        # ── 风控规则触发覆盖 ──
        if risk_trigger.get("action"):
            action = str(risk_trigger["action"])
            risk_level = "high"
            confidence = max(confidence, self._to_float(risk_trigger.get("confidence"), 0.75))
            risk_warnings.append(str(risk_trigger.get("message") or "已触发持仓风控规则。"))

        # ── 数据质量降级 ──
        data_quality = precheck.get("data_quality") or {}
        kline_count = data_quality.get("kline_count") or 0
        news_count = data_quality.get("news_count") or 0
        rag_news_count = data_quality.get("rag_news_count") or 0
        if not kline_count:
            risk_warnings.append("本地 K 线数据不足，AI 结论可信度需要下调。")
            confidence = min(confidence, 0.55)
            confidence_adjustment = "K 线数据为 0，置信度上限 0.55"
        elif kline_count < 10:
            risk_warnings.append(f"K 线仅 {kline_count} 条（不足 10 条），分析可信度受限。")
            confidence = min(confidence, 0.60)
            confidence_adjustment = f"K 线仅 {kline_count} 条，置信度上限 0.60"
        if not news_count and not rag_news_count:
            risk_warnings.append("未检索到本地关联资讯或 RAG 新闻证据，时事维度暂未覆盖。")
        elif rag_news_count:
            rag_summary = (context.get("analysis_context_summary") or {}).get("rag_news_summary") or {}
            citations = rag_summary.get("citations") or []
            first_citation = citations[0] if citations else {}
            citation_index = first_citation.get("index")
            citation_suffix = f"[{citation_index}]" if citation_index else ""
            reasons.append(f"RAG 新闻检索提供 {rag_news_count} 条可引用证据{citation_suffix}，已纳入消息面判断。")
        if not data_quality.get("has_snapshot"):
            risk_warnings.append("行情快照不可用，当前价格未知。")
            confidence = min(confidence, 0.40)
            confidence_adjustment = "行情快照不可用，置信度上限 0.40"

        # ── 分析类型差异化 summary ──
        summary_map = {
            "buy_decision": "Mock 规则分析（v3）：基于技术评分与风控规则的买入决策参考。",
            "position_review": "Mock 规则分析（v3）：基于持仓盈亏与趋势延续性的持仓复盘参考。",
            "risk_review": "Mock 规则分析（v3）：基于风险评分与多维度风控的风险审查参考。",
        }

        # ── 结构化执行计划 ──
        snapshot = context.get("snapshot") or {}
        summary = context.get("analysis_context_summary") or {}
        technical_summary = summary.get("technical_summary") or {}
        position_summary = summary.get("position_summary") or {}
        current_price = self._to_float(snapshot.get("price") or (summary.get("market_snapshot") or {}).get("current_price"), 0)
        stop_loss = "参考成本价下跌 5% 减半仓，下跌 10% 清仓"
        take_profit = "参考成本价上涨 10% 减 1/3，上涨 20% 减半，上涨 50% 清仓"
        if current_price > 0:
            stop_loss += f"；当前价 {current_price:.2f}，若跌破关键均线或触发亏损阈值需优先执行风控"
        if position_summary and position_summary.get("avg_cost"):
            avg_cost = self._to_float(position_summary.get("avg_cost"), 0)
            stop_loss += f"；持仓成本参考 {avg_cost:.2f}"
            take_profit += f"；持仓成本参考 {avg_cost:.2f}"
        watch_condition = "观察趋势评分、MA5/MA10/MA20 结构、成交量相对 5 日均量变化，以及是否出现新的关联资讯。"
        if technical_summary.get("ma_structure"):
            watch_condition = str(technical_summary["ma_structure"]) + " " + watch_condition
        entry_condition = "仅当趋势评分改善、价格重新站上关键均线且量能不明显萎缩时，再考虑 100 股最小单位试探。"
        invalid_condition = "若收盘价跌破关键均线、趋势评分继续下降、放量下跌或出现重大利空，本次判断失效。"
        review_time = "建议下一个交易日收盘后复盘；若盘中触发止损/止盈条件则立即复核。"
        if request.analysis_type == "position_review":
            entry_condition = "持仓复盘场景不建议盲目加仓；只有趋势延续且未接近止盈/止损阈值时才考虑维持仓位。"
            invalid_condition = "若触发系统止损/止盈线，或趋势由上升转为下降，本次持仓判断失效。"
        elif request.analysis_type == "risk_review":
            entry_condition = "风险审查场景以控制风险为主，暂不以买入作为默认目标。"
            invalid_condition = "若风险评分继续恶化、出现政策/业绩/流动性风险，或价格放量破位，需要重新审查。"
            review_time = "建议每日收盘后复查风险；若出现重大利空或触发风控线则立即复查。"

        return {
            "action": action,
            "confidence": round(confidence, 2),
            "risk_level": risk_level,
            "summary": summary_map.get(request.analysis_type, summary_map["buy_decision"]),
            "reasons": reasons,
            "risk_warnings": risk_warnings,
            "suggested_plan": {
                "execution": "先核对 AI 结论、当前价和风控触发状态，再由用户在交易面板手动确认，不自动下单。",
                "position_size": "A 股最小交易单位 100 股；买入默认 100 股试探，减仓按 1/3、1/2 或全仓规则取整到 100 股。",
                "entry_condition": entry_condition,
                "watch_condition": watch_condition,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "invalid_condition": invalid_condition,
                "review_time": review_time,
                "next_step": "如需执行交易，先确认价格、数量、止损/止盈线，再回到交易面板二次确认；若触发风控条件，优先按系统规则处理。",
            },
            "data_quality": {
                **data_quality,
                "mode": "mock_rules_graph",
                "confidence_adjustment": confidence_adjustment,
            },
        }

    def _build_context_summary(self, context: dict[str, Any]) -> dict[str, Any]:
        """生成面向 LLM 的结构化投研摘要，减少模型直接解读原始上下文的负担。"""
        snapshot = context.get("snapshot") or {}
        technical = context.get("technical") or {}
        latest = technical.get("latest_feature") or {}
        scores = technical.get("scores") or {}
        klines = context.get("recent_klines") or []
        news = context.get("news") or []
        rag_news_context = context.get("rag_news_context") or {}
        position = context.get("position") or {}
        account = context.get("account_summary") or {}

        close = self._to_float(latest.get("close") or snapshot.get("price"), 0.0)
        ma5 = self._to_float(latest.get("ma5"), 0.0)
        ma10 = self._to_float(latest.get("ma10"), 0.0)
        ma20 = self._to_float(latest.get("ma20"), 0.0)
        ma60 = self._to_float(latest.get("ma60"), 0.0)
        return_1 = self._to_float(latest.get("return_1"), 0.0)
        return_5 = self._to_float(latest.get("return_5"), 0.0)
        return_20 = self._to_float(latest.get("return_20"), 0.0)
        volume_ratio_5 = self._to_float(latest.get("volume_ratio_5"), 0.0)
        price_position_20 = latest.get("price_position_20")
        price_position_60 = latest.get("price_position_60")

        trend_text = self._describe_trend(latest.get("trend_direction"), scores.get("trend"), latest.get("signal"))
        ma_text = self._describe_ma_structure(close, ma5, ma10, ma20, ma60)
        volume_text = self._describe_volume(volume_ratio_5)
        position_text = self._describe_price_position(price_position_20, price_position_60)
        recent_range = self._summarize_kline_range(klines)
        position_summary = self._summarize_position(position, close)
        risk_trigger = self._risk_trigger_from_pnl(position_summary.get("unrealized_pnl_pct") if position_summary else None)
        news_summary = self._summarize_news(news)
        rag_news_summary = self._summarize_rag_news(rag_news_context)
        data_quality = self._summarize_data_quality(context)

        key_observations = [
            item for item in [
                trend_text,
                ma_text,
                volume_text,
                position_text,
                news_summary.get("headline_summary"),
                rag_news_summary.get("headline_summary"),
                position_summary.get("summary") if position_summary else None,
                risk_trigger.get("message"),
            ]
            if item
        ]

        return {
            "version": "context_summary_v1",
            "symbol": context.get("symbol"),
            "analysis_type": context.get("analysis_type"),
            "horizon": context.get("horizon"),
            "market_snapshot": {
                "current_price": close or snapshot.get("price"),
                "change_pct": snapshot.get("change_pct"),
                "snapshot_time": snapshot.get("timestamp"),
                "has_realtime_snapshot": bool(snapshot),
            },
            "technical_summary": {
                "trend": trend_text,
                "ma_structure": ma_text,
                "volume": volume_text,
                "price_position": position_text,
                "recent_range": recent_range,
                "scores": {
                    "technical": scores.get("technical"),
                    "trend": scores.get("trend"),
                    "momentum": scores.get("momentum"),
                    "risk": scores.get("risk"),
                },
                "returns": {
                    "return_1_pct": self._ratio_to_pct(return_1),
                    "return_5_pct": self._ratio_to_pct(return_5),
                    "return_20_pct": self._ratio_to_pct(return_20),
                },
                "raw_signal": latest.get("signal"),
            },
            "position_summary": position_summary,
            "risk_summary": {
                "triggered_rule": risk_trigger or None,
                "system_rules": context.get("risk_rules") or {},
            },
            "news_summary": news_summary,
            "rag_news_summary": rag_news_summary,
            "account_summary": account,
            "data_quality_summary": data_quality,
            "key_observations": key_observations,
            "llm_instruction_hint": "优先参考本 analysis_context_summary，再用原始 snapshot/technical/recent_klines/news/rag_news_context/position 交叉验证；若引用 RAG 新闻证据，必须带 [1]、[2] 等 citation；若摘要与原始数据冲突，以原始数据为准。",
        }

    def _describe_trend(self, trend_direction: Any, trend_score: Any, signal: Any) -> str:
        label_map = {
            "strong_up": "强上升趋势",
            "up": "上升趋势",
            "sideways": "震荡趋势",
            "down": "下降趋势",
            "strong_down": "强下降趋势",
            "unknown": "趋势未知",
        }
        label = label_map.get(str(trend_direction or "unknown"), str(trend_direction or "趋势未知"))
        score = self._to_float(trend_score, 50.0)
        return f"{label}，趋势评分 {score:.1f}，信号 {signal or 'neutral'}。"

    def _describe_ma_structure(self, close: float, ma5: float, ma10: float, ma20: float, ma60: float) -> str:
        if close <= 0:
            return "当前价格缺失，无法判断均线结构。"
        valid_mas = [("MA5", ma5), ("MA10", ma10), ("MA20", ma20), ("MA60", ma60)]
        valid_mas = [(name, value) for name, value in valid_mas if value > 0]
        if not valid_mas:
            return "均线数据不足，无法判断多空排列。"
        above = [name for name, value in valid_mas if close >= value]
        below = [name for name, value in valid_mas if close < value]
        if ma5 > 0 and ma10 > 0 and ma20 > 0 and close > ma5 > ma10 > ma20:
            structure = "短期多头排列"
        elif ma5 > 0 and ma10 > 0 and ma20 > 0 and close < ma5 < ma10 < ma20:
            structure = "短期空头排列"
        else:
            structure = "均线结构分化"
        return f"{structure}；当前价位于 {', '.join(above) if above else '无主要均线'} 之上，位于 {', '.join(below) if below else '无主要均线'} 之下。"

    def _describe_volume(self, volume_ratio_5: float) -> str:
        if volume_ratio_5 <= 0:
            return "量能数据不足，无法判断放量/缩量。"
        if volume_ratio_5 >= 1.5:
            return f"近一日成交量约为 5 日均量 {volume_ratio_5:.2f} 倍，明显放量。"
        if volume_ratio_5 >= 1.1:
            return f"近一日成交量约为 5 日均量 {volume_ratio_5:.2f} 倍，温和放量。"
        if volume_ratio_5 <= 0.7:
            return f"近一日成交量约为 5 日均量 {volume_ratio_5:.2f} 倍，明显缩量。"
        return f"近一日成交量约为 5 日均量 {volume_ratio_5:.2f} 倍，量能接近均衡。"

    def _describe_price_position(self, position_20: Any, position_60: Any) -> str:
        p20 = self._to_float(position_20, -1.0)
        p60 = self._to_float(position_60, -1.0)
        parts = []
        if 0 <= p20 <= 1:
            parts.append(f"20日区间位置 {p20:.0%}（0%接近低点，100%接近高点）")
        if 0 <= p60 <= 1:
            parts.append(f"60日区间位置 {p60:.0%}")
        if not parts:
            return "区间位置数据不足，无法判断当前价格高低位。"
        return "；".join(parts) + "。"

    def _summarize_kline_range(self, klines: list[dict[str, Any]]) -> dict[str, Any]:
        if not klines:
            return {"kline_count": 0, "summary": "无本地 K 线数据。"}
        closes = [self._to_float(row.get("close"), 0.0) for row in klines if self._to_float(row.get("close"), 0.0) > 0]
        highs = [self._to_float(row.get("high"), 0.0) for row in klines if self._to_float(row.get("high"), 0.0) > 0]
        lows = [self._to_float(row.get("low"), 0.0) for row in klines if self._to_float(row.get("low"), 0.0) > 0]
        if not closes:
            return {"kline_count": len(klines), "summary": "K 线存在但收盘价缺失。"}
        first_close = closes[0]
        last_close = closes[-1]
        range_return = (last_close - first_close) / first_close if first_close else 0.0
        return {
            "kline_count": len(klines),
            "first_time": klines[0].get("trade_time"),
            "last_time": klines[-1].get("trade_time"),
            "range_return_pct": self._ratio_to_pct(range_return),
            "range_high": max(highs) if highs else None,
            "range_low": min(lows) if lows else None,
            "summary": f"最近 {len(klines)} 条K线区间涨跌幅约 {self._ratio_to_pct(range_return):.2f}% 。",
        }

    def _summarize_position(self, position: dict[str, Any], current_price: float) -> dict[str, Any] | None:
        if not position:
            return None
        quantity = self._to_float(position.get("quantity"), 0.0)
        avg_cost = self._to_float(position.get("avg_cost"), 0.0)
        pnl_pct = self._to_float(position.get("unrealized_pnl_pct"), 0.0)
        if avg_cost > 0 and current_price > 0 and not position.get("unrealized_pnl_pct"):
            pnl_pct = (current_price - avg_cost) / avg_cost * 100
        return {
            "has_position": True,
            "quantity": quantity,
            "avg_cost": avg_cost,
            "current_price": current_price or position.get("current_price"),
            "market_value": position.get("market_value"),
            "unrealized_pnl": position.get("unrealized_pnl"),
            "unrealized_pnl_pct": pnl_pct,
            "strategy_mode": position.get("strategy_mode"),
            "summary": f"当前持仓 {quantity:.0f} 股，成本 {avg_cost:.2f}，浮动盈亏约 {pnl_pct:.2f}%。",
        }

    def _summarize_news(self, news: list[dict[str, Any]]) -> dict[str, Any]:
        if not news:
            return {"news_count": 0, "headline_summary": "未检索到本地关联资讯，时事维度缺失。", "items": []}
        items = []
        sentiment_count = {"positive": 0, "negative": 0, "neutral": 0, "unknown": 0}
        for item in news[:5]:
            sentiment = str(item.get("sentiment") or "unknown")
            if sentiment not in sentiment_count:
                sentiment = "unknown"
            sentiment_count[sentiment] += 1
            items.append({
                "title": item.get("title"),
                "source": item.get("source"),
                "published_at": item.get("published_at"),
                "sentiment": sentiment,
                "importance": item.get("importance"),
                "summary": item.get("summary"),
            })
        return {
            "news_count": len(news),
            "sentiment_count": sentiment_count,
            "headline_summary": f"检索到 {len(news)} 条关联资讯，前 {len(items)} 条已摘要；情绪统计 {sentiment_count}。",
            "items": items,
        }

    def _build_rag_news_context(
        self,
        *,
        service: Any,
        symbol: str,
        request: AIStockAnalysisRequest,
        technical: dict[str, Any],
        snapshot: dict[str, Any] | None,
    ) -> dict[str, Any]:
        query = self._build_rag_query(symbol=symbol, request=request, technical=technical, snapshot=snapshot)
        if service is None:
            return self._empty_rag_news_context(enabled=False, query=query, error="RAG 新闻检索服务未初始化。")
        limit = max(1, min(settings.ai_context_news_limit or 5, 5))
        result = self._safe_call(
            lambda: service.build_retrieval_context(
                query=query,
                limit=limit,
                related_symbol=symbol,
            ),
            self._empty_rag_news_context(enabled=True, query=query, error="RAG 新闻上下文检索失败。"),
        )
        return self._trim_rag_news_context(result)

    def _build_rag_query(
        self,
        *,
        symbol: str,
        request: AIStockAnalysisRequest,
        technical: dict[str, Any],
        snapshot: dict[str, Any] | None,
    ) -> str:
        latest = (technical or {}).get("latest_feature") or {}
        industry = (technical or {}).get("industry") or latest.get("industry")
        sector = (technical or {}).get("sector") or latest.get("sector")
        price = (snapshot or {}).get("price")
        type_label = {
            "buy_decision": "买入机会",
            "position_review": "持仓变化和风险",
            "risk_review": "潜在利空和风险",
        }.get(request.analysis_type, "投资机会和风险")
        parts = [symbol, type_label, "近期新闻", "政策", "业绩", "行业", "利好", "利空"]
        if industry:
            parts.append(str(industry))
        if sector:
            parts.append(str(sector))
        if price:
            parts.append(f"当前价格{price}")
        if request.user_question:
            parts.append(str(request.user_question))
        return " ".join(str(item) for item in parts if item)

    def _trim_rag_news_context(self, context: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(context, dict) or not context.get("ok"):
            return context
        max_chars = max(1000, int(settings.ai_max_rag_context_chars or 6000))
        max_citations = max(1, int(settings.ai_max_rag_citations or 5))
        context_text = str(context.get("context_text") or "")
        original_chars = len(context_text)
        citations = context.get("citations") or []
        items = context.get("items") or []
        if len(context_text) > max_chars:
            context["context_text"] = context_text[:max_chars].rstrip() + "\n...[RAG context 已按成本预算截断]"
        if isinstance(citations, list) and len(citations) > max_citations:
            context["citations"] = citations[:max_citations]
        if isinstance(items, list) and len(items) > max_citations:
            context["items"] = items[:max_citations]
        cost_guard = dict(context.get("cost_guard") or {})
        cost_guard.update({
            "max_context_chars": max_chars,
            "original_context_chars": original_chars,
            "final_context_chars": len(str(context.get("context_text") or "")),
            "max_citations": max_citations,
            "original_citation_count": len(citations) if isinstance(citations, list) else 0,
            "final_citation_count": len(context.get("citations") or []),
            "trimmed": original_chars > max_chars or (isinstance(citations, list) and len(citations) > max_citations),
        })
        context["cost_guard"] = cost_guard
        return context

    def _empty_rag_news_context(self, *, enabled: bool, query: str | None = None, error: str | None = None) -> dict[str, Any]:
        return {
            "ok": False,
            "enabled": enabled,
            "query": query,
            "count": 0,
            "context_text": "",
            "citations": [],
            "items": [],
            "error": error,
        }

    def _summarize_rag_news(self, rag_news_context: dict[str, Any]) -> dict[str, Any]:
        if not rag_news_context:
            return {"available": False, "rag_news_count": 0, "headline_summary": "RAG 新闻证据未接入。", "citations": []}
        count = int(rag_news_context.get("count") or 0)
        citations = rag_news_context.get("citations") or []
        if not rag_news_context.get("ok"):
            return {
                "available": False,
                "rag_news_count": 0,
                "headline_summary": f"RAG 新闻证据不可用：{rag_news_context.get('error') or '未知原因'}",
                "citations": [],
                "query": rag_news_context.get("query"),
            }
        citation_titles = [f"[{item.get('index')}] {item.get('title') or item.get('news_id') or item.get('chunk_id')}" for item in citations[:5]]
        return {
            "available": True,
            "rag_news_count": count,
            "headline_summary": f"RAG 检索到 {count} 条可引用新闻证据：{'；'.join(citation_titles) if citation_titles else '无引用条目'}。",
            "citations": citations[:5],
            "query": rag_news_context.get("query"),
        }

    def _rag_news_count(self, rag_news_context: Any) -> int:
        if not isinstance(rag_news_context, dict) or not rag_news_context.get("ok"):
            return 0
        return int(rag_news_context.get("count") or 0)

    def _summarize_data_quality(self, context: dict[str, Any]) -> dict[str, Any]:
        klines = context.get("recent_klines") or []
        news = context.get("news") or []
        rag_news_context = context.get("rag_news_context") or {}
        snapshot = context.get("snapshot") or {}
        position = context.get("position") or {}
        warnings: list[str] = []
        confidence_cap: float | None = None
        if not snapshot:
            warnings.append("行情快照不可用，当前价格维度缺失。")
            confidence_cap = 0.40
        if len(klines) == 0:
            warnings.append("K 线数据为 0，技术分析可信度显著受限。")
            confidence_cap = min(confidence_cap or 1.0, 0.55)
        elif len(klines) < 10:
            warnings.append(f"K 线仅 {len(klines)} 条，不足 10 条。")
            confidence_cap = min(confidence_cap or 1.0, 0.60)
        if not news and not self._rag_news_count(rag_news_context):
            warnings.append("未检索到关联资讯或 RAG 新闻证据，消息面未覆盖。")
            confidence_cap = min(confidence_cap or 1.0, 0.60)
        elif rag_news_context and not rag_news_context.get("ok"):
            warnings.append(f"RAG 新闻证据不可用：{rag_news_context.get('error') or '未知原因'}")
        return {
            "has_snapshot": bool(snapshot),
            "kline_count": len(klines),
            "news_count": len(news),
            "rag_news_count": self._rag_news_count(rag_news_context),
            "has_position": bool(position),
            "warnings": warnings,
            "suggested_confidence_cap": confidence_cap,
        }

    def _ratio_to_pct(self, value: float) -> float:
        return round(value * 100, 2)

    def _risk_trigger_from_pnl(self, pnl_pct: float | None) -> dict[str, Any]:
        if pnl_pct is None:
            return {}
        if pnl_pct <= -10:
            return {"action": "sell", "confidence": 0.8, "message": "持仓浮亏超过 10%，已触发系统清仓风控线。"}
        if pnl_pct <= -5:
            return {"action": "reduce", "confidence": 0.75, "message": "持仓浮亏超过 5%，已触发系统减半仓风控线。"}
        if pnl_pct >= 50:
            return {"action": "sell", "confidence": 0.78, "message": "持仓浮盈超过 50%，已触发系统止盈清仓线。"}
        if pnl_pct >= 20:
            return {"action": "reduce", "confidence": 0.7, "message": "持仓浮盈超过 20%，已触发系统减半止盈线。"}
        if pnl_pct >= 10:
            return {"action": "reduce", "confidence": 0.66, "message": "持仓浮盈超过 10%，已触发系统减三分之一止盈线。"}
        return {}

    def _risk_rules(self) -> dict[str, str]:
        return {
            "loss_5_reduce_half": "持仓成本价下跌超过 5% 减半仓；100 股持仓直接清仓",
            "loss_10_clear_all": "持仓成本价下跌超过 10% 清仓",
            "profit_10_reduce_one_third": "持仓成本价上涨超过 10% 减三分之一仓位",
            "profit_20_reduce_half": "持仓成本价上涨超过 20% 减半仓",
            "profit_50_clear_all": "持仓成本价上涨超过 50% 清仓",
        }

    def _append_trace(self, state: StockAnalysisState, node: str, status: str, message: str) -> None:
        state["trace"].append({
            "node": node,
            "status": status,
            "message": message,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        })

    def _snapshot_to_dict(self, snapshot: Any) -> dict[str, Any] | None:
        if snapshot is None:
            return None
        return {
            "symbol": getattr(snapshot, "symbol", None),
            "price": getattr(snapshot, "price", None),
            "change_pct": getattr(snapshot, "change_pct", None),
            "volume": getattr(snapshot, "volume", None),
            "timestamp": getattr(snapshot, "timestamp", None),
        }

    def _safe_call(self, fn: Callable[[], Any], default: Any) -> Any:
        try:
            return fn()
        except Exception:
            return default

    def _page_params(self):
        from quant_system.api.pagination import PageParams

        return PageParams(page=1, page_size=settings.ai_context_news_limit)

    def _to_float(self, value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _to_int(self, value: Any, default: int = 0) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return default

    def _normalize_symbol(self, symbol: Any) -> str:
        text = str(symbol or "").strip().upper()
        if "." in text:
            text = text.split(".")[0]
        return text.zfill(6) if text.isdigit() and len(text) < 6 else text

    def dump_state_for_debug(self, response: AIStockAnalysisResponse) -> str:
        return json.dumps(response.model_dump(), ensure_ascii=False, indent=2, default=str)
