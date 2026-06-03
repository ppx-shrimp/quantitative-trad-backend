from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from quant_system.ai.evaluation_service import AIAnalysisEvaluationService
from quant_system.ai.llm_client import OpenAICompatibleClient
from quant_system.ai.schemas import AIStockAnalysisRequest, AIStockAnalysisResponse
from quant_system.ai.workflow import StockAnalysisWorkflow
from quant_system.db.database import SessionLocal, init_sqlalchemy_tables
from quant_system.db.models import AIAnalysisRecordModel
from quant_system.rag.news_embedding_service import NewsEmbeddingService
from quant_system.services.analysis_service import AnalysisService
from quant_system.services.feature_service import FeatureService
from quant_system.services.kline_service import KlineService
from quant_system.services.news_service import NewsService
from quant_system.services.trading_service import TradingService


class AIAnalysisService:
    def __init__(self) -> None:
        init_sqlalchemy_tables()
        self.analysis_service = AnalysisService()
        self.feature_service = FeatureService()
        self.kline_service = KlineService()
        self.news_service = NewsService()
        self.trading_service = TradingService()
        self.news_embedding_service = NewsEmbeddingService()
        self.evaluation_service = AIAnalysisEvaluationService(kline_service=self.kline_service)
        self.llm_client = OpenAICompatibleClient()
        self.workflow = StockAnalysisWorkflow(
            services={
                "analysis_service": self.analysis_service,
                "feature_service": self.feature_service,
                "kline_service": self.kline_service,
                "news_service": self.news_service,
                "trading_service": self.trading_service,
                "news_embedding_service": self.news_embedding_service,
                "evaluation_service": self.evaluation_service,
            },
            llm_client=self.llm_client,
        )

    def analyze_stock(self, request: AIStockAnalysisRequest) -> AIStockAnalysisResponse:
        analysis_id = f"ai_{uuid.uuid4().hex}"
        created_at = self._now()
        symbol = self._normalize_symbol(request.symbol)
        response = self.workflow.run(
            analysis_id=analysis_id,
            symbol=symbol,
            request=request,
            created_at=created_at,
        )
        self._save_record(response, request)
        return response

    def llm_status(self) -> dict:
        return self.llm_client.status()

    def diagnose_llm(self) -> dict:
        return self.llm_client.diagnose()

    def list_recent_records(self, symbol: str | None = None, limit: int = 20, *, include_payload: bool = False) -> dict:
        stmt = select(AIAnalysisRecordModel).order_by(AIAnalysisRecordModel.created_at.desc()).limit(limit)
        if symbol:
            stmt = (
                select(AIAnalysisRecordModel)
                .where(AIAnalysisRecordModel.symbol == self._normalize_symbol(symbol))
                .order_by(AIAnalysisRecordModel.created_at.desc())
                .limit(limit)
            )
        with SessionLocal() as session:
            rows = session.scalars(stmt).all()
        return {"count": len(rows), "items": [self._record_to_dict(row, include_payload=include_payload) for row in rows]}

    def _save_record(self, response: AIStockAnalysisResponse, request: AIStockAnalysisRequest) -> None:
        now = response.created_at
        decision = response.decision
        plan = decision.suggested_plan if decision and isinstance(decision.suggested_plan, dict) else {}
        risk_constraint = self._latest_risk_constraint(decision.data_quality if decision else {})
        record = AIAnalysisRecordModel(
            analysis_id=response.analysis_id,
            symbol=response.symbol,
            analysis_type=response.analysis_type,
            action=decision.action if decision else None,
            confidence=decision.confidence if decision else None,
            risk_level=decision.risk_level if decision else None,
            plan_execution=self._plan_text(plan, "execution"),
            plan_position_size=self._plan_text(plan, "position_size"),
            plan_entry_condition=self._plan_text(plan, "entry_condition"),
            plan_watch_condition=self._plan_text(plan, "watch_condition"),
            plan_stop_loss=self._plan_text(plan, "stop_loss"),
            plan_take_profit=self._plan_text(plan, "take_profit"),
            plan_invalid_condition=self._plan_text(plan, "invalid_condition"),
            plan_review_time=self._plan_text(plan, "review_time"),
            plan_next_step=self._plan_text(plan, "next_step"),
            risk_constraint_triggered=bool(risk_constraint),
            risk_forced_action=self._constraint_text(risk_constraint, "forced_action"),
            risk_original_action=self._constraint_text(risk_constraint, "original_action"),
            risk_trigger_message=self._constraint_text(risk_constraint, "trigger_message"),
            risk_original_confidence=self._constraint_float(risk_constraint, "original_confidence"),
            risk_final_confidence=self._constraint_float(risk_constraint, "final_confidence"),
            risk_constraint_json=json.dumps(risk_constraint, ensure_ascii=False, default=str) if risk_constraint else None,
            model_provider=response.provider,
            model_name=response.model,
            prompt_version=response.prompt_version,
            status=response.status,
            input_json=json.dumps(request.model_dump(), ensure_ascii=False, default=str),
            context_json=json.dumps(response.context, ensure_ascii=False, default=str),
            output_json=json.dumps(response.raw_output, ensure_ascii=False, default=str),
            error_message=response.error_message,
            created_at=now,
            updated_at=now,
            created_by="system",
            updated_by="system",
        )
        with SessionLocal() as session:
            session.add(record)
            session.commit()

    def _record_to_dict(self, row: AIAnalysisRecordModel, *, include_payload: bool = False) -> dict[str, Any]:
        item = {
            "analysis_id": row.analysis_id,
            "symbol": row.symbol,
            "analysis_type": row.analysis_type,
            "action": row.action,
            "confidence": row.confidence,
            "risk_level": row.risk_level,
            "plan_execution": row.plan_execution,
            "plan_position_size": row.plan_position_size,
            "plan_entry_condition": row.plan_entry_condition,
            "plan_watch_condition": row.plan_watch_condition,
            "plan_stop_loss": row.plan_stop_loss,
            "plan_take_profit": row.plan_take_profit,
            "plan_invalid_condition": row.plan_invalid_condition,
            "plan_review_time": row.plan_review_time,
            "plan_next_step": row.plan_next_step,
            "risk_constraint_triggered": row.risk_constraint_triggered,
            "risk_forced_action": row.risk_forced_action,
            "risk_original_action": row.risk_original_action,
            "risk_trigger_message": row.risk_trigger_message,
            "risk_original_confidence": row.risk_original_confidence,
            "risk_final_confidence": row.risk_final_confidence,
            "risk_constraint": self._parse_json(row.risk_constraint_json),
            "linked_order_id": getattr(row, "linked_order_id", None),
            "linked_order_status": getattr(row, "linked_order_status", None),
            "linked_order_side": getattr(row, "linked_order_side", None),
            "linked_order_quantity": getattr(row, "linked_order_quantity", None),
            "linked_order_price": getattr(row, "linked_order_price", None),
            "linked_order_at": getattr(row, "linked_order_at", None),
            "linked_order": self._parse_json(getattr(row, "linked_order_json", None)),
            "model_provider": row.model_provider,
            "model_name": row.model_name,
            "prompt_version": row.prompt_version,
            "status": row.status,
            "error_message": row.error_message,
            "created_at": row.created_at,
        }
        if include_payload:
            item.update({
                "input": self._parse_json(row.input_json),
                "context": self._parse_json(row.context_json),
                "output": self._parse_json(row.output_json),
            })
        return item

    def _plan_text(self, plan: dict[str, Any], key: str) -> str | None:
        value = plan.get(key)
        if value is None:
            return None
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, default=str)
        text = str(value).strip()
        return text or None

    def _latest_risk_constraint(self, data_quality: dict[str, Any]) -> dict[str, Any]:
        value = (data_quality or {}).get("hard_risk_constraints")
        if isinstance(value, list):
            items = [item for item in value if isinstance(item, dict)]
            return items[-1] if items else {}
        return value if isinstance(value, dict) else {}

    def _constraint_text(self, constraint: dict[str, Any], key: str) -> str | None:
        value = constraint.get(key)
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _constraint_float(self, constraint: dict[str, Any], key: str) -> float | None:
        value = constraint.get(key)
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _parse_json(self, value: str | None) -> Any:
        if not value:
            return None
        try:
            return json.loads(value)
        except Exception:
            return None

    def _normalize_symbol(self, symbol: Any) -> str:
        text = str(symbol or "").strip().upper()
        if "." in text:
            text = text.split(".")[0]
        return text.zfill(6) if text.isdigit() and len(text) < 6 else text

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()
