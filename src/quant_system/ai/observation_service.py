from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from quant_system.ai.recommendation_service import AIRecommendationService
from quant_system.ai.schemas import AIStockAnalysisRequest
from quant_system.ai.service import AIAnalysisService
from quant_system.db.database import SessionLocal, init_sqlalchemy_tables
from quant_system.db.models import AIObservationCandidateModel
from quant_system.services.kline_service import KlineService


class AIObservationService:
    """AI 观察池 / 候选股工作流。

    第一版不引入外部编排系统，只把已有股票池推荐、单股 AI 分析、RAG 证据、
    交易单预填入口串成一个可落库、可复查、可关闭的候选股闭环。
    """

    ACTIVE_STATUSES = {"watching", "triggered", "reviewing"}
    CLOSED_STATUSES = {"dismissed", "converted", "archived"}
    VALID_STATUSES = ACTIVE_STATUSES | CLOSED_STATUSES

    def __init__(self) -> None:
        init_sqlalchemy_tables()
        self.recommendation_service = AIRecommendationService()
        self.ai_service = AIAnalysisService()
        self.kline_service = KlineService()

    def scan_pool(
        self,
        *,
        pool_code: str = "favorites",
        limit: int = 10,
        period: str = "daily",
        style: str = "steady_watch",
        run_deep_analysis: bool = False,
    ) -> dict[str, Any]:
        limit = max(1, min(int(limit or 10), 50))
        scan_id = f"obs_scan_{uuid.uuid4().hex}"
        now = self._now()
        recommendation = self.recommendation_service.recommend_from_pool(
            pool_code=pool_code,
            limit=limit,
            period=period,
            style=style,
        )
        recommendation_items = recommendation.get("items") or []
        stored_items: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []

        with SessionLocal() as session:
            for item in recommendation_items:
                symbol = self._normalize_symbol(item.get("symbol"))
                if not symbol:
                    skipped.append({"symbol": item.get("symbol"), "reason": "股票代码为空或非法"})
                    continue

                ai_record = None
                if run_deep_analysis and item.get("action") != "avoid_now":
                    ai_record = self._run_analysis(symbol)

                row = self._upsert_candidate(
                    session,
                    item=item,
                    scan_id=scan_id,
                    pool_code=pool_code,
                    period=period,
                    style=style,
                    now=now,
                    ai_record=ai_record,
                )
                stored_items.append(self._model_to_dict(row))
            session.commit()

        return {
            "ok": True,
            "mode": "ai_observation_pool_mvp",
            "scan_id": scan_id,
            "pool_code": pool_code,
            "period": period,
            "style": style,
            "run_deep_analysis": run_deep_analysis,
            "generated_at": now,
            "count": len(stored_items),
            "items": stored_items,
            "skipped": skipped + (recommendation.get("skipped") or []),
            "summary": self._scan_summary(stored_items, run_deep_analysis=run_deep_analysis),
            "disclaimer": "AI 观察池仅用于候选跟踪、复查和交易单预填，不会自动下单；任何交易仍需人工确认。",
            "dify_advice": self.dify_advice(),
        }

    def list_candidates(
        self,
        *,
        status: str | None = None,
        pool_code: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        limit = max(1, min(int(limit or 50), 200))
        with SessionLocal() as session:
            stmt = select(AIObservationCandidateModel)
            if status and status != "all":
                if status == "active":
                    stmt = stmt.where(AIObservationCandidateModel.status.in_(self.ACTIVE_STATUSES))
                else:
                    stmt = stmt.where(AIObservationCandidateModel.status == status)
            if pool_code:
                stmt = stmt.where(AIObservationCandidateModel.pool_code == pool_code)
            rows = session.scalars(
                stmt.order_by(
                    AIObservationCandidateModel.status.asc(),
                    AIObservationCandidateModel.recommendation_score.desc(),
                    AIObservationCandidateModel.updated_at.desc(),
                ).limit(limit)
            ).all()
        items = [self._model_to_dict(row) for row in rows]
        return {
            "ok": True,
            "count": len(items),
            "summary": self._list_summary(items),
            "items": items,
            "dify_advice": self.dify_advice(),
        }

    def update_candidate(
        self,
        candidate_id: str,
        *,
        status: str | None = None,
        note: str | None = None,
        linked_order_id: str | None = None,
    ) -> dict[str, Any]:
        now = self._now()
        with SessionLocal() as session:
            row = session.scalar(select(AIObservationCandidateModel).where(AIObservationCandidateModel.candidate_id == candidate_id))
            if row is None:
                raise ValueError("观察候选不存在")
            if status is not None:
                normalized_status = status.strip().lower()
                if normalized_status not in self.VALID_STATUSES:
                    raise ValueError("不支持的观察池状态")
                if row.status != normalized_status:
                    row.status_changed_at = now
                row.status = normalized_status
                if normalized_status in {"reviewing", "triggered"}:
                    row.last_reviewed_at = row.last_reviewed_at or now
                if normalized_status in self.CLOSED_STATUSES:
                    row.next_check_at = None
            if note is not None:
                row.note = note
            if linked_order_id is not None:
                row.linked_order_id = linked_order_id
                if row.status != "converted":
                    row.status_changed_at = now
                row.status = "converted"
                row.next_check_at = None
            row.updated_at = now
            row.updated_by = "user"
            session.commit()
            session.refresh(row)
            return self._model_to_dict(row)

    def reanalyze_candidate(self, candidate_id: str) -> dict[str, Any]:
        now = self._now()
        with SessionLocal() as session:
            row = session.scalar(select(AIObservationCandidateModel).where(AIObservationCandidateModel.candidate_id == candidate_id))
            if row is None:
                raise ValueError("观察候选不存在")
            analysis = self._run_analysis(row.symbol)
            row.analysis_id = analysis.get("analysis_id")
            row.ai_action = analysis.get("action") or row.ai_action
            row.confidence = analysis.get("confidence") if analysis.get("confidence") is not None else row.confidence
            row.risk_level = analysis.get("risk_level") or row.risk_level
            if analysis.get("summary"):
                row.summary = analysis.get("summary")
            next_status = self._status_from_analysis(row.status, analysis)
            if row.status != next_status:
                row.status_changed_at = now
            row.status = next_status
            row.trigger_reason = self._analysis_trigger_reason(analysis) or row.trigger_reason
            row.last_reviewed_at = now
            row.next_check_at = self._next_check_at(row.status, now)
            row.updated_at = now
            row.updated_by = "system"
            payload = self._json(row.payload_json)
            payload["latest_analysis"] = analysis
            row.payload_json = json.dumps(payload, ensure_ascii=False, default=str)
            session.commit()
            session.refresh(row)
            return {"ok": True, "item": self._model_to_dict(row), "analysis": analysis}

    def track_candidates(
        self,
        *,
        limit: int = 50,
        only_due: bool = True,
        run_deep_analysis_on_trigger: bool = False,
    ) -> dict[str, Any]:
        limit = max(1, min(int(limit or 50), 200))
        now = self._now()
        checked_items: list[dict[str, Any]] = []
        transitions: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []

        with SessionLocal() as session:
            stmt = select(AIObservationCandidateModel).where(AIObservationCandidateModel.status.in_(self.ACTIVE_STATUSES))
            if only_due:
                stmt = stmt.where(
                    (AIObservationCandidateModel.next_check_at.is_(None))
                    | (AIObservationCandidateModel.next_check_at <= now)
                )
            rows = session.scalars(
                stmt.order_by(
                    AIObservationCandidateModel.next_check_at.asc(),
                    AIObservationCandidateModel.recommendation_score.desc(),
                    AIObservationCandidateModel.updated_at.desc(),
                ).limit(limit)
            ).all()

            for row in rows:
                before = row.status
                try:
                    tracking = self._evaluate_tracking(row)
                    next_status = self._status_from_tracking(row, tracking)
                    if next_status != row.status:
                        row.status = next_status
                        row.status_changed_at = now
                        transitions.append({
                            "candidate_id": row.candidate_id,
                            "symbol": row.symbol,
                            "from": before,
                            "to": next_status,
                            "reason": tracking.get("trigger_reason") or tracking.get("summary"),
                        })
                    row.current_price = tracking.get("current_price") if tracking.get("current_price") is not None else row.current_price
                    row.trigger_reason = tracking.get("trigger_reason") or row.trigger_reason
                    row.tracking_json = json.dumps(tracking, ensure_ascii=False, default=str)
                    row.last_tracked_at = now
                    row.next_check_at = self._next_check_at(row.status, now)
                    row.updated_at = now
                    row.updated_by = "system"

                    if run_deep_analysis_on_trigger and row.status == "triggered":
                        analysis = self._run_analysis(row.symbol)
                        row.analysis_id = analysis.get("analysis_id") or row.analysis_id
                        row.ai_action = analysis.get("action") or row.ai_action
                        row.confidence = analysis.get("confidence") if analysis.get("confidence") is not None else row.confidence
                        row.risk_level = analysis.get("risk_level") or row.risk_level
                        if analysis.get("summary"):
                            row.summary = analysis.get("summary")
                        analysis_status = self._status_from_analysis(row.status, analysis)
                        if analysis_status != row.status:
                            row.status = analysis_status
                            row.status_changed_at = now
                        row.trigger_reason = self._analysis_trigger_reason(analysis) or row.trigger_reason
                        row.next_check_at = self._next_check_at(row.status, now)
                        payload = self._json(row.payload_json)
                        payload["latest_analysis"] = analysis
                        row.payload_json = json.dumps(payload, ensure_ascii=False, default=str)
                    checked_items.append(self._model_to_dict(row))
                except Exception as exc:
                    errors.append({"candidate_id": row.candidate_id, "symbol": row.symbol, "error": str(exc)})
            session.commit()

        return {
            "ok": len(errors) == 0,
            "mode": "ai_observation_tracking",
            "generated_at": now,
            "checked_count": len(checked_items),
            "transition_count": len(transitions),
            "error_count": len(errors),
            "summary": self._tracking_summary(checked_items, transitions, errors),
            "transitions": transitions,
            "errors": errors,
            "items": checked_items,
        }

    def dify_advice(self) -> dict[str, Any]:
        return {
            "recommended_for_mvp": False,
            "decision": "第一版不建议接入 Dify，先在现有 FastAPI + AI workflow 内实现观察池闭环。",
            "reason": "观察池核心是候选落库、状态流转、交易纪律、RAG 证据和订单预填联动，这些都依赖当前业务数据库和交易服务；过早接入 Dify 会增加部署、鉴权、状态同步和调试复杂度。",
            "when_to_consider": "当后续需要运营同学可视化调整 Prompt、多 Agent 编排、多模型灰度、跨渠道通知或非工程化工作流配置时，再把 Dify 作为外部编排层接入。",
        }

    def _upsert_candidate(
        self,
        session: Any,
        *,
        item: dict[str, Any],
        scan_id: str,
        pool_code: str,
        period: str,
        style: str,
        now: str,
        ai_record: dict[str, Any] | None,
    ) -> AIObservationCandidateModel:
        symbol = self._normalize_symbol(item.get("symbol"))
        dedupe_key = f"{pool_code}:{symbol}:{period}:{style}"
        row = session.scalar(select(AIObservationCandidateModel).where(AIObservationCandidateModel.dedupe_key == dedupe_key))
        if row is None:
            row = AIObservationCandidateModel(
                candidate_id=f"obs_{uuid.uuid4().hex}",
                dedupe_key=dedupe_key,
                created_at=now,
                updated_at=now,
                created_by="system",
                updated_by="system",
            )
            session.add(row)
        elif row.status in self.CLOSED_STATUSES:
            row.status = "watching"

        action = ai_record.get("action") if ai_record else item.get("action")
        confidence = ai_record.get("confidence") if ai_record and ai_record.get("confidence") is not None else item.get("confidence")
        risk_level = ai_record.get("risk_level") if ai_record else item.get("risk_level")
        score = self._to_float(item.get("recommendation_score"))
        payload = {
            "recommendation": item,
            "latest_analysis": ai_record,
            "period": period,
            "style": style,
            "scan_id": scan_id,
        }

        row.scan_id = scan_id
        row.symbol = symbol
        row.stock_name = item.get("name") or symbol
        row.pool_code = pool_code
        row.source_type = "pool_scan"
        row.status = self._initial_status(item, ai_record)
        row.recommendation_score = score
        row.ai_action = action
        row.confidence = self._to_float(confidence)
        row.risk_level = risk_level
        row.title = self._title(item, ai_record)
        row.summary = ai_record.get("summary") if ai_record and ai_record.get("summary") else item.get("summary")
        row.reasons_json = json.dumps(item.get("reasons") or [], ensure_ascii=False, default=str)
        row.risk_notes_json = json.dumps(item.get("risk_notes") or [], ensure_ascii=False, default=str)
        row.suggested_next_step = self._next_step(item, ai_record)
        row.trigger_price = self._to_float((item.get("latest_feature") or {}).get("close"))
        row.current_price = row.trigger_price
        row.analysis_id = ai_record.get("analysis_id") if ai_record else row.analysis_id
        row.payload_json = json.dumps(payload, ensure_ascii=False, default=str)
        if row.status_changed_at is None:
            row.status_changed_at = now
        row.next_check_at = self._next_check_at(row.status, now)
        row.updated_at = now
        row.updated_by = "system"
        return row

    def _run_analysis(self, symbol: str) -> dict[str, Any]:
        try:
            response = self.ai_service.analyze_stock(AIStockAnalysisRequest(
                symbol=symbol,
                analysis_type="buy_decision",
                horizon="1-5d",
                include_news=True,
                include_position=True,
                user_question="请从观察池候选角度复核该股票是否值得继续观察，并给出明确触发条件、风险失效条件和下一步人工动作。",
            ))
        except Exception as exc:
            return {"status": "failed", "symbol": symbol, "error_message": str(exc)}
        decision = response.decision
        plan = decision.suggested_plan if decision else {}
        return {
            "analysis_id": response.analysis_id,
            "status": response.status,
            "symbol": response.symbol,
            "action": decision.action if decision else None,
            "confidence": decision.confidence if decision else None,
            "risk_level": decision.risk_level if decision else None,
            "summary": decision.summary if decision else response.error_message,
            "plan_next_step": plan.get("next_step") if isinstance(plan, dict) else None,
            "plan_watch_condition": plan.get("watch_condition") if isinstance(plan, dict) else None,
            "plan_invalid_condition": plan.get("invalid_condition") if isinstance(plan, dict) else None,
            "error_message": response.error_message,
        }

    def _initial_status(self, item: dict[str, Any], ai_record: dict[str, Any] | None) -> str:
        if ai_record and ai_record.get("status") == "failed":
            return "watching"
        action = ai_record.get("action") if ai_record else item.get("action")
        risk_level = ai_record.get("risk_level") if ai_record else item.get("risk_level")
        score = self._to_float(item.get("recommendation_score")) or 0
        if action in {"buy", "watch", "watch_first"} and risk_level != "high" and score >= 72:
            return "triggered"
        if action == "observe" and risk_level != "high" and score >= 72:
            return "triggered"
        if action in {"avoid", "avoid_now", "sell", "reduce"} or risk_level == "high":
            return "reviewing"
        return "watching"

    def _status_from_analysis(self, current_status: str, analysis: dict[str, Any]) -> str:
        if analysis.get("status") == "failed":
            return current_status
        if analysis.get("action") in {"buy", "watch", "watch_first", "observe"} and analysis.get("risk_level") != "high":
            return "triggered"
        if analysis.get("action") in {"avoid", "sell", "reduce"} or analysis.get("risk_level") == "high":
            return "reviewing"
        return "watching"

    def _evaluate_tracking(self, row: AIObservationCandidateModel) -> dict[str, Any]:
        try:
            klines = self.kline_service.list_klines(row.symbol, period="daily", limit=20)
        except Exception as exc:
            return {
                "ok": False,
                "symbol": row.symbol,
                "current_price": row.current_price,
                "trigger_price": row.trigger_price,
                "signals": [],
                "risk_flags": [f"K 线读取失败：{exc}"],
                "trigger_reason": f"K 线读取失败：{exc}",
                "summary": "无法读取最新 K 线，建议人工复核数据源。",
            }

        latest = klines[-1] if klines else {}
        previous = klines[-2] if len(klines) >= 2 else {}
        last5 = klines[-5:] if len(klines) >= 5 else klines
        current_price = self._to_float(latest.get("close")) or row.current_price
        previous_close = self._to_float(previous.get("close"))
        trigger_price = row.trigger_price or current_price
        change_from_trigger_pct = self._pct_change(current_price, trigger_price)
        day_change_pct = self._pct_change(current_price, previous_close)
        first5_close = self._to_float(last5[0].get("close")) if last5 else None
        five_day_change_pct = self._pct_change(current_price, first5_close)
        latest_volume = self._to_float(latest.get("volume")) or self._to_float(latest.get("vol"))
        avg_volume_5d = self._avg([
            self._to_float(item.get("volume")) or self._to_float(item.get("vol"))
            for item in last5[:-1]
        ])
        volume_ratio_5d = (latest_volume / avg_volume_5d) if latest_volume and avg_volume_5d else None

        trigger_conditions = [
            self._condition(
                code="price_from_pool_up",
                label="入池后上涨",
                actual=change_from_trigger_pct,
                threshold=3,
                unit="%",
                comparator=">=",
                severity="trigger",
                explanation="相对入池价上涨达到 3%，说明候选开始兑现观察逻辑，适合做 AI/人工复查。",
            ),
            self._condition(
                code="five_day_trend_up",
                label="近 5 日趋势",
                actual=five_day_change_pct,
                threshold=4,
                unit="%",
                comparator=">=",
                severity="trigger",
                explanation="近 5 日涨幅达到 4%，说明短线趋势增强，需要确认是否追高或等待回踩。",
            ),
            self._condition(
                code="volume_breakout",
                label="放量上行",
                actual=volume_ratio_5d,
                threshold=1.8,
                unit="x",
                comparator=">=",
                severity="trigger",
                extra_actual={"day_change_pct": day_change_pct},
                triggered=volume_ratio_5d is not None and volume_ratio_5d >= 1.8 and (day_change_pct or 0) > 0,
                explanation="量比达到 1.8 且当日上涨，说明资金参与度提升，但仍需结合风险和位置判断。",
            ),
        ]
        risk_conditions = [
            self._condition(
                code="price_from_pool_down",
                label="入池后回撤",
                actual=change_from_trigger_pct,
                threshold=-5,
                unit="%",
                comparator="<=",
                severity="risk",
                explanation="相对入池价下跌超过 5%，观察逻辑可能失效，优先进入风险复核。",
            ),
            self._condition(
                code="five_day_trend_down",
                label="近 5 日走弱",
                actual=five_day_change_pct,
                threshold=-6,
                unit="%",
                comparator="<=",
                severity="risk",
                explanation="近 5 日跌幅超过 6%，短线趋势偏弱，需要检查是否应放弃或延后观察。",
            ),
            {
                "code": "ai_high_risk",
                "label": "AI 高风险",
                "actual": row.risk_level,
                "threshold": "high",
                "unit": "",
                "comparator": "==",
                "triggered": row.risk_level == "high",
                "severity": "risk",
                "explanation": "AI 风险等级为 high 时，优先保护模拟账户纪律，不直接进入交易预填。",
            },
            {
                "code": "ai_avoid_action",
                "label": "AI 规避动作",
                "actual": row.ai_action,
                "threshold": "avoid/reduce/sell",
                "unit": "",
                "comparator": "in",
                "triggered": row.ai_action in {"avoid", "avoid_now", "sell", "reduce"},
                "severity": "risk",
                "explanation": "AI 动作为规避、减仓或卖出时，说明当前更适合复核风险而不是寻找买点。",
            },
        ]
        triggered_conditions = [item for item in trigger_conditions if item.get("triggered")]
        triggered_risks = [item for item in risk_conditions if item.get("triggered")]
        signals = [self._condition_reason(item) for item in triggered_conditions]
        risk_flags = [self._condition_reason(item) for item in triggered_risks]

        trigger_reason = "；".join(risk_flags or signals)
        if risk_flags:
            summary = "跟踪发现风险信号，建议进入风险复核。"
            signal_explanation = {
                "verdict": "risk_review",
                "label": "风险优先",
                "primary_reason": risk_flags[0],
                "next_action": "先看风险条件和失效条件，必要时放弃观察或延后复查。",
            }
        elif signals:
            summary = "跟踪发现触发信号，建议进行 AI/人工复查。"
            signal_explanation = {
                "verdict": "trigger_review",
                "label": "触发复查",
                "primary_reason": signals[0],
                "next_action": "先做单股 AI 复查，再决定是否仅预填交易单并人工确认。",
            }
        elif current_price is not None:
            summary = "暂未触发明显信号，继续观察。"
            signal_explanation = {
                "verdict": "keep_watching",
                "label": "继续观察",
                "primary_reason": "价格、趋势和量能暂未达到触发阈值。",
                "next_action": "等待下一次到期跟踪，或手动查看 K 线确认是否需要调整观察逻辑。",
            }
        else:
            summary = "暂无可用价格，继续等待数据更新。"
            signal_explanation = {
                "verdict": "data_pending",
                "label": "等待数据",
                "primary_reason": "当前没有可用价格，无法判断触发条件。",
                "next_action": "检查行情数据同步后再跟踪。",
            }

        return {
            "ok": True,
            "symbol": row.symbol,
            "latest_trade_time": latest.get("trade_time") or latest.get("date"),
            "current_price": current_price,
            "trigger_price": trigger_price,
            "change_from_trigger_pct": change_from_trigger_pct,
            "day_change_pct": day_change_pct,
            "five_day_change_pct": five_day_change_pct,
            "volume_ratio_5d": volume_ratio_5d,
            "signals": signals,
            "risk_flags": risk_flags,
            "trigger_conditions": trigger_conditions,
            "risk_conditions": risk_conditions,
            "signal_explanation": signal_explanation,
            "trigger_reason": trigger_reason,
            "summary": summary,
        }

    def _condition(
        self,
        *,
        code: str,
        label: str,
        actual: float | None,
        threshold: float,
        unit: str,
        comparator: str,
        severity: str,
        explanation: str,
        extra_actual: dict[str, Any] | None = None,
        triggered: bool | None = None,
    ) -> dict[str, Any]:
        if triggered is None:
            if actual is None:
                triggered = False
            elif comparator == ">=":
                triggered = actual >= threshold
            elif comparator == "<=":
                triggered = actual <= threshold
            else:
                triggered = False
        return {
            "code": code,
            "label": label,
            "actual": actual,
            "threshold": threshold,
            "unit": unit,
            "comparator": comparator,
            "triggered": triggered,
            "severity": severity,
            "explanation": explanation,
            "extra_actual": extra_actual or {},
        }

    def _condition_reason(self, condition: dict[str, Any]) -> str:
        actual = condition.get("actual")
        unit = condition.get("unit") or ""
        label = condition.get("label") or condition.get("code")
        if isinstance(actual, (int, float)):
            if unit == "x":
                return f"{label} {actual:.2f}{unit}"
            return f"{label} {actual:.2f}{unit}"
        if actual:
            return f"{label}：{actual}"
        return str(label)

    def _status_from_tracking(self, row: AIObservationCandidateModel, tracking: dict[str, Any]) -> str:
        if row.status in self.CLOSED_STATUSES:
            return row.status
        if tracking.get("risk_flags"):
            return "reviewing"
        if tracking.get("signals"):
            return "triggered"
        return row.status or "watching"

    def _next_check_at(self, status: str, now: str) -> str | None:
        if status in self.CLOSED_STATUSES:
            return None
        base = self._parse_time(now)
        if status == "triggered":
            return (base + timedelta(hours=4)).isoformat()
        if status == "reviewing":
            return (base + timedelta(hours=12)).isoformat()
        return (base + timedelta(hours=24)).isoformat()

    def _analysis_trigger_reason(self, analysis: dict[str, Any]) -> str | None:
        if analysis.get("status") == "failed":
            return analysis.get("error_message") or "AI 复查失败"
        if analysis.get("risk_level") == "high":
            return "AI 复查提示高风险"
        if analysis.get("action") in {"buy", "watch", "watch_first", "observe"}:
            return analysis.get("plan_watch_condition") or analysis.get("summary")
        if analysis.get("action") in {"avoid", "sell", "reduce"}:
            return analysis.get("plan_invalid_condition") or analysis.get("summary")
        return analysis.get("summary")

    def _tracking_summary(
        self,
        checked_items: list[dict[str, Any]],
        transitions: list[dict[str, Any]],
        errors: list[dict[str, Any]],
    ) -> str:
        if errors and not checked_items:
            return f"观察池跟踪失败：{len(errors)} 个候选读取或判断异常。"
        triggered = sum(1 for item in checked_items if item.get("status") == "triggered")
        reviewing = sum(1 for item in checked_items if item.get("status") == "reviewing")
        return f"观察池跟踪完成：检查 {len(checked_items)} 个候选，{len(transitions)} 个发生状态流转，当前 {triggered} 个触发复查、{reviewing} 个风险复核，异常 {len(errors)} 个。"

    def _pct_change(self, current: Any, base: Any) -> float | None:
        current_value = self._to_float(current)
        base_value = self._to_float(base)
        if current_value is None or base_value in (None, 0):
            return None
        return (current_value - base_value) / base_value * 100

    def _avg(self, values: list[float | None]) -> float | None:
        valid = [value for value in values if value is not None]
        if not valid:
            return None
        return sum(valid) / len(valid)

    def _parse_time(self, value: str) -> datetime:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return datetime.now(timezone.utc)

    def _model_to_dict(self, row: AIObservationCandidateModel) -> dict[str, Any]:
        payload = self._json(row.payload_json)
        return {
            "id": row.id,
            "candidate_id": row.candidate_id,
            "scan_id": row.scan_id,
            "symbol": row.symbol,
            "stock_name": row.stock_name,
            "pool_code": row.pool_code,
            "source_type": row.source_type,
            "status": row.status,
            "recommendation_score": row.recommendation_score,
            "ai_action": row.ai_action,
            "confidence": row.confidence,
            "risk_level": row.risk_level,
            "title": row.title,
            "summary": row.summary,
            "reasons": self._json_list(row.reasons_json),
            "risk_notes": self._json_list(row.risk_notes_json),
            "suggested_next_step": row.suggested_next_step,
            "trigger_price": row.trigger_price,
            "current_price": row.current_price,
            "analysis_id": row.analysis_id,
            "linked_order_id": row.linked_order_id,
            "last_reviewed_at": row.last_reviewed_at,
            "last_tracked_at": row.last_tracked_at,
            "next_check_at": row.next_check_at,
            "trigger_reason": row.trigger_reason,
            "status_changed_at": row.status_changed_at,
            "tracking": self._json(row.tracking_json),
            "note": row.note,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "latest_analysis": payload.get("latest_analysis"),
            "recommendation": payload.get("recommendation"),
        }

    def _scan_summary(self, items: list[dict[str, Any]], *, run_deep_analysis: bool) -> str:
        triggered = sum(1 for item in items if item.get("status") == "triggered")
        reviewing = sum(1 for item in items if item.get("status") == "reviewing")
        return f"观察池扫描完成：新增/更新 {len(items)} 个候选，{triggered} 个进入触发复查，{reviewing} 个需要风险复核。{'已执行单股 AI 深度复核。' if run_deep_analysis else '当前为轻量技术筛选，可对重点候选再点复查。'}"

    def _list_summary(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "total": len(items),
            "watching_count": sum(1 for item in items if item.get("status") == "watching"),
            "triggered_count": sum(1 for item in items if item.get("status") == "triggered"),
            "reviewing_count": sum(1 for item in items if item.get("status") == "reviewing"),
            "converted_count": sum(1 for item in items if item.get("status") == "converted"),
            "dismissed_count": sum(1 for item in items if item.get("status") == "dismissed"),
            "archived_count": sum(1 for item in items if item.get("status") == "archived"),
        }

    def _title(self, item: dict[str, Any], ai_record: dict[str, Any] | None) -> str:
        name = item.get("name") or item.get("symbol") or "候选股"
        if ai_record and ai_record.get("action"):
            return f"{name}：AI 复核为 {ai_record.get('action')}"
        return f"{name}：{item.get('summary') or '进入观察池'}"

    def _next_step(self, item: dict[str, Any], ai_record: dict[str, Any] | None) -> str:
        if ai_record and ai_record.get("plan_next_step"):
            return str(ai_record.get("plan_next_step"))
        return str(item.get("suggested_next_step") or "先做 AI 单股复查，再决定是否带入交易面板手动确认。")

    def _json(self, value: str | None) -> dict[str, Any]:
        if not value:
            return {}
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}

    def _json_list(self, value: str | None) -> list[Any]:
        if not value:
            return []
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []

    def _to_float(self, value: Any) -> float | None:
        if value in (None, "", "-"):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _normalize_symbol(self, value: Any) -> str:
        return str(value or "").strip().upper().split(".")[0]

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()
