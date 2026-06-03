from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import or_, select

from quant_system.ai.service import AIAnalysisService
from quant_system.db.database import SessionLocal, init_sqlalchemy_tables
from quant_system.db.models import AlertTodoModel
from quant_system.services.trading_service import TradingService


class AlertTodoService:
    """预警待办聚合服务。

    扫描当前持仓风控状态和最近 AI 分析记录，生成即落库；通过 dedupe_key 避免
    同一股票/同一规则在未闭环前反复生成重复待办。
    """

    ACTIVE_STATUSES = {"open", "acknowledged", "reviewing", "snoozed"}
    CLOSED_STATUSES = {"resolved", "ignored"}
    VALID_STATUSES = ACTIVE_STATUSES | CLOSED_STATUSES

    def __init__(self) -> None:
        init_sqlalchemy_tables()
        self.trading_service = TradingService()
        self.ai_service = AIAnalysisService()

    def list_todos(self, *, limit_ai_records: int = 100) -> dict[str, Any]:
        generated = self._generate_todos(limit_ai_records=limit_ai_records)
        now = self._now()
        with SessionLocal() as session:
            for item in generated:
                self._upsert_generated_todo(session, item, now)
            session.commit()

            rows = session.scalars(
                select(AlertTodoModel)
                .where(
                    or_(
                        AlertTodoModel.status.in_(self.ACTIVE_STATUSES),
                        AlertTodoModel.updated_at >= now[:10],
                    )
                )
                .order_by(AlertTodoModel.status.asc(), AlertTodoModel.severity.asc(), AlertTodoModel.updated_at.desc())
            ).all()
            items = [self._model_to_dict(row) for row in rows]

        items = [self._apply_noise_policy(item, now) for item in items if self._is_visible(item, now)]
        items.sort(key=lambda item: (item.get("priority_rank", 9), item.get("noise_rank", 9), item.get("updated_at") or item.get("created_at") or ""))
        summary = self._summary(items)
        return {"summary": summary, "count": len(items), "items": items}

    def update_todo(
        self,
        todo_id: str,
        *,
        status: str | None = None,
        note: str | None = None,
        snooze_until: str | None = None,
        linked_order_id: str | None = None,
    ) -> dict[str, Any]:
        now = self._now()
        with SessionLocal() as session:
            row = session.scalar(select(AlertTodoModel).where(AlertTodoModel.todo_id == todo_id))
            if row is None:
                raise ValueError("待办不存在")

            if status is not None:
                status = status.strip().lower()
                if status not in self.VALID_STATUSES:
                    raise ValueError("不支持的待办状态")
                row.status = status
                if status == "acknowledged":
                    row.acknowledged_at = row.acknowledged_at or now
                elif status == "reviewing":
                    row.acknowledged_at = row.acknowledged_at or now
                elif status == "snoozed":
                    row.acknowledged_at = row.acknowledged_at or now
                    row.snooze_until = snooze_until or row.snooze_until
                elif status == "resolved":
                    row.resolved_at = row.resolved_at or now
                elif status == "ignored":
                    row.ignored_at = row.ignored_at or now

            if note is not None:
                row.note = note
            if snooze_until is not None:
                row.snooze_until = snooze_until
            if linked_order_id is not None:
                row.linked_order_id = linked_order_id
                if status is None:
                    row.status = "reviewing"
                    row.acknowledged_at = row.acknowledged_at or now
                elif status == "resolved":
                    row.resolved_at = row.resolved_at or now

            if row.status in {"open", "acknowledged", "reviewing"}:
                row.snooze_until = None
            row.updated_at = now
            row.updated_by = "user"
            session.commit()
            session.refresh(row)
            return self._model_to_dict(row)

    def _generate_todos(self, *, limit_ai_records: int) -> list[dict[str, Any]]:
        positions_payload = self.trading_service.list_positions()
        positions = positions_payload.get("positions") or []
        ai_records = self.ai_service.list_recent_records(limit=limit_ai_records).get("items") or []
        latest_ai_by_symbol = self._latest_ai_by_symbol(ai_records)

        items: list[dict[str, Any]] = []
        for pos in positions:
            item = self._position_risk_todo(pos, latest_ai_by_symbol.get(self._normalize_symbol(pos.get("symbol") or pos.get("ts_code"))))
            if item:
                items.append(item)

        position_symbols = {self._normalize_symbol(pos.get("symbol") or pos.get("ts_code")) for pos in positions}
        for record in ai_records:
            symbol = self._normalize_symbol(record.get("symbol"))
            if not symbol or symbol in position_symbols:
                continue
            item = self._ai_record_todo(record)
            if item:
                items.append(item)
        return items

    def _upsert_generated_todo(self, session: Any, item: dict[str, Any], now: str) -> AlertTodoModel:
        dedupe_key = item["dedupe_key"]
        row = session.scalar(select(AlertTodoModel).where(AlertTodoModel.dedupe_key == dedupe_key))
        if row is None:
            row = AlertTodoModel(
                todo_id=item.get("todo_id") or f"todo_{uuid.uuid4().hex}",
                dedupe_key=dedupe_key,
                created_at=now,
                updated_at=now,
                created_by="system",
                updated_by="system",
            )
            session.add(row)
        elif row.status in self.CLOSED_STATUSES:
            return row

        existing_payload = self._json(row.payload_json)
        first_seen_at = existing_payload.get("first_seen_at") or row.created_at or now
        previous_repeat_count = int(existing_payload.get("repeat_count") or 0)
        repeat_count = previous_repeat_count + 1
        last_notified_at = existing_payload.get("last_notified_at") or row.created_at or now
        previous_cooldown_until = existing_payload.get("cooldown_until")
        if previous_repeat_count > 0 and (not previous_cooldown_until or previous_cooldown_until <= now):
            last_notified_at = now
        cooldown_until = self._cooldown_until(item, last_notified_at)
        is_cooled_down = repeat_count > 1 and cooldown_until > now
        item.update({
            "first_seen_at": first_seen_at,
            "last_seen_at": now,
            "last_notified_at": last_notified_at,
            "repeat_count": repeat_count,
            "cooldown_until": cooldown_until,
            "noise_level": self._noise_level(item),
            "is_cooled_down": is_cooled_down,
        })
        payload_json = json.dumps(item, ensure_ascii=False, default=str)

        if row.status not in self.CLOSED_STATUSES:
            row.source_type = item.get("source_type") or row.source_type
            row.source_id = item.get("source_id")
            row.symbol = item.get("symbol") or row.symbol
            row.stock_name = item.get("stock_name")
            row.severity = item.get("severity") or row.severity
            row.title = item.get("title") or row.title
            row.message = item.get("message")
            row.suggested_action = item.get("suggested_action")
            row.suggested_direction = item.get("suggested_direction")
            row.suggested_quantity = item.get("suggested_quantity")
            row.current_price = item.get("current_price")
            row.avg_cost = item.get("avg_cost")
            row.pnl_pct = item.get("pnl_pct")
            row.action_required = bool(item.get("action_required"))
            row.analysis_id = item.get("analysis_id")
            row.payload_json = payload_json
            row.updated_at = now
            row.updated_by = "system"
        return row

    def _position_risk_todo(self, pos: dict[str, Any], ai_record: dict[str, Any] | None) -> dict[str, Any] | None:
        symbol = self._normalize_symbol(pos.get("symbol") or pos.get("ts_code"))
        quantity = int(pos.get("quantity") or 0)
        pnl_pct = self._pnl_ratio(pos.get("unrealized_pnl_pct"))
        current_price = self._float(pos.get("current_price") or pos.get("price") or pos.get("avg_price"))
        avg_price = self._float(pos.get("avg_price") or pos.get("avg_cost"))
        ai_risky = bool(ai_record and (ai_record.get("risk_level") == "high" or ai_record.get("action") in {"sell", "reduce", "avoid"} or ai_record.get("risk_constraint_triggered")))

        severity = ""
        title = ""
        message = ""
        suggested_action = "review"
        suggested_quantity = 0
        action_required = False

        if pnl_pct <= -0.10:
            severity = "critical"
            title = "触发 -10% 清仓线"
            message = f"当前浮亏 {self._pct(pnl_pct)}，按固定风控规则应清仓。"
            suggested_action = "sell_all"
            suggested_quantity = quantity
            action_required = True
        elif pnl_pct <= -0.05:
            severity = "warning"
            title = "触发 -5% 减半仓线"
            message = f"当前浮亏 {self._pct(pnl_pct)}，按固定风控规则应减半仓。"
            suggested_action = "reduce_half"
            suggested_quantity = self._round_lot(quantity / 2)
            action_required = True
        elif pnl_pct >= 0.50:
            severity = "critical"
            title = "触发 +50% 清仓止盈线"
            message = f"当前浮盈 {self._pct(pnl_pct)}，按固定风控规则应清仓止盈。"
            suggested_action = "sell_all"
            suggested_quantity = quantity
            action_required = True
        elif pnl_pct >= 0.20:
            severity = "warning"
            title = "触发 +20% 减半止盈线"
            message = f"当前浮盈 {self._pct(pnl_pct)}，按固定风控规则应减半仓。"
            suggested_action = "reduce_half"
            suggested_quantity = self._round_lot(quantity / 2)
            action_required = True
        elif pnl_pct >= 0.10:
            severity = "warning"
            title = "触发 +10% 减 1/3 止盈线"
            message = f"当前浮盈 {self._pct(pnl_pct)}，按固定风控规则应减三分之一仓位。"
            suggested_action = "reduce_third"
            suggested_quantity = self._round_lot(quantity / 3)
            action_required = True
        elif pnl_pct <= -0.04:
            severity = "watch"
            title = "接近 -5% 减仓线"
            message = f"当前浮亏 {self._pct(pnl_pct)}，接近固定减仓阈值。"
            suggested_action = "watch_risk"
            suggested_quantity = self._round_lot(quantity / 2)
        elif pnl_pct >= 0.08:
            severity = "watch"
            title = "接近 +10% 止盈线"
            message = f"当前浮盈 {self._pct(pnl_pct)}，接近分批止盈阈值。"
            suggested_action = "watch_profit"
            suggested_quantity = self._round_lot(quantity / 3)
        elif ai_risky:
            severity = "watch"
            title = "AI 给出风险提示"
            message = "固定风控线未触发，但最近 AI 分析提示高风险、减仓、卖出或硬约束。"
            suggested_action = "ai_review"
            suggested_quantity = self._round_lot(quantity / 2)
        else:
            return None

        dedupe_key = f"position_risk:{symbol}:{suggested_action}"
        return {
            "todo_id": f"position:{symbol}:{suggested_action}",
            "dedupe_key": dedupe_key,
            "source_type": "position_risk",
            "source_id": symbol,
            "severity": severity,
            "status": "open",
            "action_required": action_required,
            "symbol": symbol,
            "stock_name": pos.get("stock_name") or pos.get("name") or symbol,
            "title": title,
            "message": message,
            "suggested_action": suggested_action,
            "suggested_direction": "sell",
            "suggested_quantity": suggested_quantity,
            "current_price": current_price,
            "avg_cost": avg_price,
            "pnl_pct": pnl_pct,
            "analysis_id": ai_record.get("analysis_id") if ai_record else None,
            "ai_action": ai_record.get("action") if ai_record else None,
            "ai_risk_level": ai_record.get("risk_level") if ai_record else None,
            "ai_plan_next_step": ai_record.get("plan_next_step") if ai_record else None,
            "created_at": ai_record.get("created_at") if ai_record else None,
        }

    def _ai_record_todo(self, record: dict[str, Any]) -> dict[str, Any] | None:
        if record.get("status") != "success" or record.get("linked_order_id"):
            return None
        action = record.get("action")
        risk_level = record.get("risk_level")
        hard_constraint = bool(record.get("risk_constraint_triggered"))
        if not (hard_constraint or risk_level == "high" or action in {"sell", "reduce", "avoid"}):
            return None
        analysis_id = record.get("analysis_id")
        severity = "warning" if hard_constraint or action in {"sell", "reduce"} else "watch"
        return {
            "todo_id": f"ai:{analysis_id}",
            "dedupe_key": f"ai_analysis:{analysis_id}",
            "source_type": "ai_analysis",
            "source_id": analysis_id,
            "severity": severity,
            "status": "open",
            "action_required": hard_constraint or action in {"sell", "reduce"},
            "symbol": record.get("symbol"),
            "stock_name": record.get("symbol"),
            "title": "AI 分析风险待复核",
            "message": record.get("risk_trigger_message") or record.get("plan_next_step") or "AI 分析提示风险，需要人工复核。",
            "suggested_action": "ai_review",
            "suggested_direction": "sell" if action in {"sell", "reduce"} else None,
            "suggested_quantity": None,
            "current_price": None,
            "avg_cost": None,
            "pnl_pct": None,
            "analysis_id": analysis_id,
            "ai_action": action,
            "ai_risk_level": risk_level,
            "ai_plan_next_step": record.get("plan_next_step"),
            "created_at": record.get("created_at"),
        }

    def _model_to_dict(self, row: AlertTodoModel) -> dict[str, Any]:
        payload = self._json(row.payload_json)
        return {
            "id": row.id,
            "todo_id": row.todo_id,
            "dedupe_key": row.dedupe_key,
            "source_type": row.source_type,
            "source_id": row.source_id,
            "severity": row.severity,
            "status": row.status,
            "action_required": row.action_required,
            "symbol": row.symbol,
            "stock_name": row.stock_name,
            "title": row.title,
            "message": row.message,
            "suggested_action": row.suggested_action,
            "suggested_direction": row.suggested_direction,
            "suggested_quantity": row.suggested_quantity,
            "current_price": row.current_price,
            "avg_cost": row.avg_cost,
            "pnl_pct": row.pnl_pct,
            "analysis_id": row.analysis_id,
            "linked_order_id": row.linked_order_id,
            "snooze_until": row.snooze_until,
            "acknowledged_at": row.acknowledged_at,
            "resolved_at": row.resolved_at,
            "ignored_at": row.ignored_at,
            "note": row.note,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "first_seen_at": payload.get("first_seen_at"),
            "last_seen_at": payload.get("last_seen_at"),
            "last_notified_at": payload.get("last_notified_at"),
            "cooldown_until": payload.get("cooldown_until"),
            "repeat_count": payload.get("repeat_count") or 0,
            "noise_level": payload.get("noise_level") or "normal",
            "is_cooled_down": bool(payload.get("is_cooled_down")),
            "ai_action": payload.get("ai_action"),
            "ai_risk_level": payload.get("ai_risk_level"),
            "ai_plan_next_step": payload.get("ai_plan_next_step"),
        }

    def _summary(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        active_items = [item for item in items if item.get("status") in self.ACTIVE_STATUSES]
        visible_items = [item for item in active_items if not item.get("is_cooled_down")]
        cooled_items = [item for item in active_items if item.get("is_cooled_down")]
        return {
            "total": len(active_items),
            "visible_count": len(visible_items),
            "cooled_count": len(cooled_items),
            "critical_count": sum(1 for item in active_items if item.get("severity") == "critical"),
            "warning_count": sum(1 for item in active_items if item.get("severity") == "warning"),
            "watch_count": sum(1 for item in active_items if item.get("severity") == "watch"),
            "position_count": sum(1 for item in active_items if item.get("source_type") == "position_risk"),
            "ai_count": sum(1 for item in active_items if item.get("source_type") == "ai_analysis"),
            "action_required_count": sum(1 for item in active_items if item.get("action_required")),
            "noise_reduced_count": len(cooled_items) + sum(1 for item in active_items if item.get("noise_level") == "low"),
        }

    def _apply_noise_policy(self, item: dict[str, Any], now: str) -> dict[str, Any]:
        item["is_cooled_down"] = bool(item.get("cooldown_until") and item["cooldown_until"] > now)
        item["noise_level"] = item.get("noise_level") or self._noise_level(item)
        item["priority_rank"] = self._priority_rank(item)
        item["noise_rank"] = 1 if item.get("is_cooled_down") else 0
        return item

    def _noise_level(self, item: dict[str, Any]) -> str:
        if item.get("severity") == "watch" or not item.get("action_required"):
            return "low"
        if item.get("severity") == "critical":
            return "high"
        return "normal"

    def _cooldown_until(self, item: dict[str, Any], last_notified_at: str) -> str:
        base = self._parse_time(last_notified_at) or datetime.now(timezone.utc)
        if item.get("severity") == "critical" and item.get("action_required"):
            hours = 1
        elif item.get("severity") == "warning" and item.get("action_required"):
            hours = 4
        else:
            hours = 24
        return (base + timedelta(hours=hours)).isoformat()

    def _priority_rank(self, item: dict[str, Any]) -> int:
        if item.get("is_cooled_down"):
            return 8 + self._severity_rank(item.get("severity"))
        if item.get("status") == "reviewing":
            return 3
        if item.get("action_required"):
            return self._severity_rank(item.get("severity"))
        return 5 + self._severity_rank(item.get("severity"))

    def _parse_time(self, value: Any) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    def _is_visible(self, item: dict[str, Any], now: str) -> bool:
        if item.get("status") == "snoozed" and item.get("snooze_until") and item["snooze_until"] > now:
            return False
        return True

    def _latest_ai_by_symbol(self, records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for record in records:
            symbol = self._normalize_symbol(record.get("symbol"))
            if symbol and symbol not in result and record.get("status") == "success":
                result[symbol] = record
        return result

    def _status_rank(self, status: Any) -> int:
        return {"open": 0, "acknowledged": 1, "reviewing": 2, "snoozed": 3, "resolved": 4, "ignored": 5}.get(str(status), 9)

    def _severity_rank(self, severity: Any) -> int:
        return {"critical": 0, "warning": 1, "watch": 2}.get(str(severity), 3)

    def _pnl_ratio(self, value: Any) -> float:
        number = self._float(value) or 0.0
        return number / 100 if abs(number) > 1 else number

    def _round_lot(self, quantity: float) -> int:
        return max(100, int(quantity // 100) * 100)

    def _float(self, value: Any) -> float | None:
        if value in (None, "", "-"):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _pct(self, value: float) -> str:
        return f"{value * 100:.2f}%"

    def _normalize_symbol(self, symbol: Any) -> str:
        return str(symbol or "").strip().upper().split(".")[0]

    def _json(self, value: str | None) -> dict[str, Any]:
        if not value:
            return {}
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()
