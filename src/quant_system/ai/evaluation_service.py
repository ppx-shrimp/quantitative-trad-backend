"""AI 分析记录复盘/命中率评估服务。

基于 AIAnalysisRecordModel 的历史建议和本地日 K 线，计算建议发出后 1/3/5 个交易日的风险收益表现。
当前采用“风险收益综合”评分：方向收益、最大回撤/最大反向波动、置信度、风险等级共同决定命中状态。
"""
from __future__ import annotations

import json
from datetime import datetime
from statistics import pstdev
from typing import Any

from sqlalchemy import select

from quant_system.db.database import SessionLocal, init_sqlalchemy_tables
from quant_system.db.models import AIAnalysisRecordModel
from quant_system.services.kline_service import KlineService


class AIAnalysisEvaluationService:
    def __init__(self, kline_service: KlineService | None = None) -> None:
        init_sqlalchemy_tables()
        self.kline_service = kline_service or KlineService()

    def evaluate_records(
        self,
        *,
        symbol: str | None = None,
        limit: int = 50,
        horizons: list[int] | None = None,
    ) -> dict[str, Any]:
        horizons = horizons or [1, 3, 5]
        records = self._load_records(symbol=symbol, limit=limit)
        items = [self.evaluate_record(row, horizons=horizons) for row in records]
        completed = [item for item in items if item.get("status") == "evaluated"]
        return self._build_summary(items=items, completed=completed, horizons=horizons, mode="live")

    def evaluate_history_samples(
        self,
        *,
        symbol: str | None = None,
        limit: int = 30,
        horizons: list[int] | None = None,
    ) -> dict[str, Any]:
        """用历史 K 线构造可立即验证的样本，便于开发阶段校验复盘评分逻辑。"""
        horizons = horizons or [1, 3, 5]
        records = self._load_records(symbol=symbol, limit=limit)
        items: list[dict[str, Any]] = []
        max_horizon = max(horizons) if horizons else 5
        for row in records:
            samples = self.evaluate_record_history_samples(row, horizons=horizons, max_samples_per_record=3)
            if samples:
                items.extend(samples)
            else:
                base = self._record_base(row)
                items.append({
                    **base,
                    "status": "pending",
                    "sample_mode": "history",
                    "message": f"本地日 K 线不足，无法构造至少包含 {max_horizon} 个后续交易日的历史样本",
                })
            if len(items) >= limit:
                items = items[:limit]
                break
        completed = [item for item in items if item.get("status") == "evaluated"]
        return self._build_summary(items=items, completed=completed, horizons=horizons, mode="history")

    def evaluate_record_history_samples(
        self,
        row: AIAnalysisRecordModel,
        *,
        horizons: list[int],
        max_samples_per_record: int = 3,
    ) -> list[dict[str, Any]]:
        base = self._record_base(row)
        if row.status != "success" or not row.action:
            return [{**base, "status": "skipped", "sample_mode": "history", "message": "仅评估成功且包含 action 的 AI 分析记录"}]

        max_horizon = max(horizons) if horizons else 5
        klines = self.kline_service.list_klines(row.symbol, period="daily", limit=520)
        if len(klines) <= max_horizon:
            return []

        candidates = self._history_anchor_indexes(klines, max_horizon=max_horizon, max_samples=max_samples_per_record)
        items = []
        for sample_no, anchor_index in enumerate(candidates, start=1):
            item = self._evaluate_record_at_anchor(
                row,
                horizons=horizons,
                klines=klines,
                anchor_index=anchor_index,
                base={
                    **base,
                    "analysis_id": f"{row.analysis_id}::history::{sample_no}",
                    "source_analysis_id": row.analysis_id,
                    "sample_mode": "history",
                    "sample_no": sample_no,
                },
            )
            items.append(item)
        return items

    def _build_summary(
        self,
        *,
        items: list[dict[str, Any]],
        completed: list[dict[str, Any]],
        horizons: list[int],
        mode: str,
    ) -> dict[str, Any]:
        hit_items = [item for item in completed if item.get("overall", {}).get("hit") is True]
        miss_items = [item for item in completed if item.get("overall", {}).get("hit") is False]
        avg_score = round(sum(item["overall"]["score"] for item in completed) / len(completed), 4) if completed else None
        score_distribution = self._score_distribution(completed)
        groups = self._group_stats(completed)
        risk_constraint_stats = self._risk_constraint_stats(items, completed)
        return {
            "mode": mode,
            "count": len(items),
            "evaluated_count": len(completed),
            "pending_count": len(items) - len(completed),
            "hit_count": len(hit_items),
            "miss_count": len(miss_items),
            "hit_rate": round(len(hit_items) / len(completed), 4) if completed else None,
            "avg_score": avg_score,
            "score_distribution": score_distribution,
            "quality_label": self._quality_label(avg_score, len(completed)),
            "quality_summary": self._quality_summary(avg_score, len(completed), len(items), score_distribution),
            "horizons": horizons,
            "groups": groups,
            "risk_constraint_stats": risk_constraint_stats,
            "feedback": self._feedback_summary(
                completed=completed,
                avg_score=avg_score,
                groups=groups,
                risk_constraint_stats=risk_constraint_stats,
            ),
            "items": items,
        }

    def evaluate_one(self, analysis_id: str, horizons: list[int] | None = None) -> dict[str, Any]:
        horizons = horizons or [1, 3, 5]
        with SessionLocal() as session:
            row = session.scalar(select(AIAnalysisRecordModel).where(AIAnalysisRecordModel.analysis_id == analysis_id))
        if row is None:
            return {"analysis_id": analysis_id, "status": "not_found", "message": "AI 分析记录不存在"}
        return self.evaluate_record(row, horizons=horizons)

    def evaluate_record(self, row: AIAnalysisRecordModel, *, horizons: list[int]) -> dict[str, Any]:
        base = self._record_base(row)
        if row.status != "success" or not row.action:
            return {**base, "status": "skipped", "message": "仅评估成功且包含 action 的 AI 分析记录"}

        klines = self.kline_service.list_klines(row.symbol, period="daily", limit=260)
        anchor_index = self._find_anchor_index(klines, row.created_at)
        if anchor_index is None:
            return {**base, "status": "pending", "message": "未找到分析日期之后的可评估 K 线"}

        return self._evaluate_record_at_anchor(
            row,
            horizons=horizons,
            klines=klines,
            anchor_index=anchor_index,
            base=base,
        )

    def _evaluate_record_at_anchor(
        self,
        row: AIAnalysisRecordModel,
        *,
        horizons: list[int],
        klines: list[dict[str, Any]],
        anchor_index: int,
        base: dict[str, Any],
    ) -> dict[str, Any]:
        anchor = klines[anchor_index]
        anchor_close = self._to_float(anchor.get("close"), 0.0)
        if anchor_close <= 0:
            return {**base, "status": "pending", "message": "分析锚点 K 线收盘价不可用"}

        horizon_results: dict[str, Any] = {}
        scores: list[float] = []
        for horizon in horizons:
            result = self._evaluate_horizon(
                action=str(row.action),
                risk_level=row.risk_level,
                confidence=row.confidence,
                klines=klines,
                anchor_index=anchor_index,
                horizon=horizon,
            )
            horizon_results[str(horizon)] = result
            if result.get("status") == "evaluated":
                scores.append(float(result["score"]))

        if not scores:
            return {
                **base,
                "status": "pending",
                "anchor": self._kline_brief(anchor),
                "horizons": horizon_results,
                "message": "后续 K 线不足，等待更多交易日后评估",
            }

        overall_score = round(sum(scores) / len(scores), 4)
        weighted_score = self._weighted_overall_score(horizon_results)
        score_confidence = self._score_confidence(horizon_results, row.confidence)
        hit = weighted_score >= 0.58
        return {
            **base,
            "status": "evaluated",
            "anchor": self._kline_brief(anchor),
            "horizons": horizon_results,
            "overall": {
                "score": weighted_score,
                "raw_avg_score": overall_score,
                "hit": hit,
                "grade": self._grade(weighted_score),
                "score_confidence": score_confidence,
                "summary": self._overall_summary(row.action, weighted_score, hit),
                "reason": self._overall_reason(row.action, horizon_results, weighted_score),
                "review_advice": self._review_advice(row.action, weighted_score, score_confidence, hit),
            },
        }

    def _evaluate_horizon(
        self,
        *,
        action: str,
        risk_level: str | None,
        confidence: float | None,
        klines: list[dict[str, Any]],
        anchor_index: int,
        horizon: int,
    ) -> dict[str, Any]:
        target_index = anchor_index + horizon
        if target_index >= len(klines):
            return {"status": "pending", "message": f"缺少分析后第 {horizon} 个交易日 K 线"}

        anchor = klines[anchor_index]
        target = klines[target_index]
        window = klines[anchor_index + 1: target_index + 1]
        anchor_close = self._to_float(anchor.get("close"), 0.0)
        target_close = self._to_float(target.get("close"), 0.0)
        if anchor_close <= 0 or target_close <= 0:
            return {"status": "pending", "message": "锚点或目标 K 线收盘价不可用"}

        forward_return = (target_close - anchor_close) / anchor_close
        highs = [self._to_float(item.get("high"), 0.0) for item in window]
        lows = [self._to_float(item.get("low"), 0.0) for item in window]
        max_upside = (max(highs) - anchor_close) / anchor_close if highs else forward_return
        max_drawdown = (min(lows) - anchor_close) / anchor_close if lows else forward_return
        daily_returns = self._daily_returns([anchor] + window)
        realized_volatility = pstdev(daily_returns) if len(daily_returns) >= 2 else 0.0

        direction_score = self._direction_score(action, forward_return)
        drawdown_score = self._drawdown_score(action, max_drawdown, max_upside)
        opportunity_score = self._opportunity_score(action, forward_return, max_upside, max_drawdown)
        confidence_score = self._confidence_score(confidence, direction_score)
        risk_score = self._risk_score(risk_level, realized_volatility, max_drawdown)
        score = round(
            direction_score * 0.38
            + drawdown_score * 0.22
            + opportunity_score * 0.15
            + confidence_score * 0.12
            + risk_score * 0.13,
            4,
        )
        return {
            "status": "evaluated",
            "horizon_days": horizon,
            "target": self._kline_brief(target),
            "forward_return_pct": self._ratio_to_pct(forward_return),
            "max_upside_pct": self._ratio_to_pct(max_upside),
            "max_drawdown_pct": self._ratio_to_pct(max_drawdown),
            "realized_volatility_pct": self._ratio_to_pct(realized_volatility),
            "score": score,
            "hit": score >= 0.58,
            "verdict": self._horizon_verdict(action, score, forward_return, max_upside, max_drawdown),
            "reason": self._horizon_reason(action, score, forward_return, max_upside, max_drawdown, realized_volatility),
            "score_breakdown": {
                "direction_score": round(direction_score, 4),
                "drawdown_score": round(drawdown_score, 4),
                "opportunity_score": round(opportunity_score, 4),
                "confidence_score": round(confidence_score, 4),
                "risk_score": round(risk_score, 4),
            },
            "score_weights": {
                "direction_score": 0.38,
                "drawdown_score": 0.22,
                "opportunity_score": 0.15,
                "confidence_score": 0.12,
                "risk_score": 0.13,
            },
        }

    def _direction_score(self, action: str, forward_return: float) -> float:
        if action in {"buy", "hold"}:
            return self._clamp(0.5 + forward_return * 8)
        if action in {"avoid", "reduce", "sell"}:
            return self._clamp(0.5 - forward_return * 8)
        # watch/observe 不强判方向，小幅波动说明观察判断合理；大涨/大跌都意味着机会或风险被低估。
        return self._clamp(1 - abs(forward_return) * 8)

    def _drawdown_score(self, action: str, max_drawdown: float, max_upside: float) -> float:
        if action in {"buy", "hold"}:
            # 看多建议需要控制回撤，5% 回撤开始明显扣分。
            return self._clamp(1 + max_drawdown * 10)
        if action in {"avoid", "reduce", "sell"}:
            # 看空/规避建议如果后续大幅上行，说明规避机会成本高。
            return self._clamp(1 - max_upside * 8)
        return self._clamp(1 - max(abs(max_drawdown), abs(max_upside)) * 4)

    def _opportunity_score(self, action: str, forward_return: float, max_upside: float, max_drawdown: float) -> float:
        if action in {"buy", "hold"}:
            # 看多结论不只看收盘收益，也奖励窗口内出现过的上行空间。
            return self._clamp(0.45 + max(max_upside, forward_return) * 6)
        if action in {"avoid", "reduce", "sell"}:
            # 规避/减仓类结论奖励后续没有错过明显上行，并适度奖励风险真的释放。
            avoided_loss = abs(min(max_drawdown, forward_return, 0.0))
            opportunity_cost = max(max_upside, forward_return, 0.0)
            return self._clamp(0.58 + avoided_loss * 5 - opportunity_cost * 6)
        # 观察类结论：小波动高分；如果出现大幅上行/下跌，说明应更早给出买入或风险动作。
        movement = max(abs(max_upside), abs(max_drawdown), abs(forward_return))
        return self._clamp(0.9 - movement * 5)

    def _confidence_score(self, confidence: float | None, direction_score: float) -> float:
        conf = self._clamp(float(confidence or 0.5))
        # 高置信且方向对加分；高置信但方向错扣分；低置信影响较小
        return self._clamp(0.5 + (direction_score - 0.5) * (0.5 + conf))

    def _risk_score(self, risk_level: str | None, volatility: float, max_drawdown: float) -> float:
        base_by_level = {"low": 0.7, "medium": 0.6, "high": 0.5}
        base = base_by_level.get(str(risk_level or "medium"), 0.6)
        volatility_penalty = min(0.25, volatility * 4)
        drawdown_penalty = min(0.35, abs(min(max_drawdown, 0.0)) * 4)
        return self._clamp(base - volatility_penalty - drawdown_penalty + 0.2)

    def _weighted_overall_score(self, horizon_results: dict[str, Any]) -> float:
        scored: list[tuple[int, float]] = []
        for key, item in horizon_results.items():
            if item.get("status") != "evaluated" or item.get("score") is None:
                continue
            try:
                horizon = int(key)
            except (TypeError, ValueError):
                horizon = int(item.get("horizon_days") or 1)
            scored.append((horizon, float(item["score"])))
        if not scored:
            return 0.0
        scored.sort(key=lambda row: row[0])
        max_horizon = max(horizon for horizon, _score in scored)
        weighted_total = 0.0
        weight_sum = 0.0
        for horizon, score in scored:
            weight = 1.0 + (horizon / max_horizon) * 0.5
            weighted_total += score * weight
            weight_sum += weight
        return round(weighted_total / weight_sum, 4)

    def _score_confidence(self, horizon_results: dict[str, Any], confidence: float | None) -> str:
        evaluated = [item for item in horizon_results.values() if item.get("status") == "evaluated"]
        if len(evaluated) >= 3 and confidence is not None:
            return "high"
        if len(evaluated) >= 2:
            return "medium"
        return "low"

    def _horizon_verdict(self, action: str, score: float, forward_return: float, max_upside: float, max_drawdown: float) -> str:
        if score >= 0.72:
            return "strong_hit"
        if score >= 0.58:
            return "hit"
        if action in {"buy", "hold"} and max_drawdown <= -0.05:
            return "risk_miss"
        if action in {"avoid", "reduce", "sell"} and max_upside >= 0.05:
            return "opportunity_miss"
        if action not in {"buy", "hold", "avoid", "reduce", "sell"} and max(abs(max_upside), abs(max_drawdown), abs(forward_return)) >= 0.05:
            return "action_too_conservative"
        return "miss"

    def _horizon_reason(
        self,
        action: str,
        score: float,
        forward_return: float,
        max_upside: float,
        max_drawdown: float,
        volatility: float,
    ) -> str:
        if score >= 0.58:
            return f"{action} 结论与后续行情基本匹配，区间收益 {self._ratio_to_pct(forward_return):.2f}%，最大上行 {self._ratio_to_pct(max_upside):.2f}%，最大回撤 {self._ratio_to_pct(max_drawdown):.2f}%。"
        if action in {"buy", "hold"}:
            return f"看多结论复盘偏弱：区间收益 {self._ratio_to_pct(forward_return):.2f}%，最大回撤 {self._ratio_to_pct(max_drawdown):.2f}%，波动 {self._ratio_to_pct(volatility):.2f}%。"
        if action in {"avoid", "reduce", "sell"}:
            return f"规避/减仓结论复盘偏弱：后续最大上行 {self._ratio_to_pct(max_upside):.2f}%，可能存在机会成本。"
        return f"观察结论复盘偏弱：后续波动幅度较大，最大上行 {self._ratio_to_pct(max_upside):.2f}%，最大回撤 {self._ratio_to_pct(max_drawdown):.2f}%，应更明确给出触发动作。"

    def _overall_reason(self, action: str | None, horizon_results: dict[str, Any], score: float) -> str:
        evaluated = [item for item in horizon_results.values() if item.get("status") == "evaluated"]
        if not evaluated:
            return "后续行情不足，暂不能形成可靠复盘解释。"
        best = max(evaluated, key=lambda item: float(item.get("score") or 0))
        worst = min(evaluated, key=lambda item: float(item.get("score") or 0))
        trend = "整体匹配" if score >= 0.58 else "整体偏弱"
        return f"{action or '-'} 建议复盘{trend}；最好窗口 {best.get('horizon_days')} 日评分 {best.get('score')}，最弱窗口 {worst.get('horizon_days')} 日评分 {worst.get('score')}。{worst.get('reason') or ''}"

    def _review_advice(self, action: str | None, score: float, score_confidence: str, hit: bool) -> str:
        if score_confidence == "low":
            return "样本窗口较少，仅作初步参考，建议等待更多交易日后再判断模型质量。"
        if hit and score >= 0.72:
            return "该类结论近期复盘较好，可继续观察类似信号，但仍需人工确认交易。"
        if hit:
            return "结论基本有效，建议继续积累样本并重点查看低分窗口原因。"
        if action in {"buy", "hold"}:
            return "看多结论偏弱，建议检查入场条件、止损阈值和是否过度相信短期上涨。"
        if action in {"avoid", "reduce", "sell"}:
            return "规避/减仓结论偏弱，建议检查是否过度保守或风险触发条件过宽。"
        return "观察类结论偏弱，建议让 AI 输出更明确的触发价、失效条件和复查周期。"

    def _score_distribution(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        buckets = {
            "A": {"label": "A ≥0.75", "count": 0},
            "B": {"label": "B 0.58-0.75", "count": 0},
            "C": {"label": "C 0.45-0.58", "count": 0},
            "D": {"label": "D <0.45", "count": 0},
        }
        for item in items:
            grade = item.get("overall", {}).get("grade")
            if grade in buckets:
                buckets[grade]["count"] += 1
        total = len(items)
        return {
            key: {**value, "ratio": round(value["count"] / total, 4) if total else None}
            for key, value in buckets.items()
        }

    def _quality_label(self, avg_score: float | None, evaluated_count: int) -> str:
        if not evaluated_count:
            return "waiting"
        if evaluated_count < 5:
            return "sample_small"
        if avg_score is None:
            return "unknown"
        if avg_score >= 0.72:
            return "strong"
        if avg_score >= 0.58:
            return "usable"
        if avg_score >= 0.45:
            return "needs_review"
        return "weak"

    def _quality_summary(
        self,
        avg_score: float | None,
        evaluated_count: int,
        total_count: int,
        distribution: dict[str, Any],
    ) -> str:
        if not evaluated_count:
            return "当前还没有足够后续 K 线完成复盘，评分为空属于正常状态。"
        if evaluated_count < 5:
            return f"当前仅 {evaluated_count}/{total_count} 条完成复盘，样本偏少，结论仅作参考。"
        strong_count = distribution.get("A", {}).get("count", 0)
        weak_count = distribution.get("D", {}).get("count", 0)
        return f"已复盘 {evaluated_count}/{total_count} 条，平均评分 {avg_score:.2f}，A 级 {strong_count} 条，D 级 {weak_count} 条。"

    def _group_stats(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "by_analysis_type": self._group_by(items, "analysis_type"),
            "by_action": self._group_by(items, "action"),
            "by_risk_level": self._group_by(items, "risk_level"),
            "by_confidence_bucket": self._group_by_confidence_bucket(items),
        }

    def _group_by(self, items: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
        buckets: dict[str, list[dict[str, Any]]] = {}
        for item in items:
            group_key = str(item.get(key) or "unknown")
            buckets.setdefault(group_key, []).append(item)

        rows = []
        for group_key, group_items in buckets.items():
            scores = [float(item["overall"]["score"]) for item in group_items if item.get("overall")]
            hit_count = sum(1 for item in group_items if item.get("overall", {}).get("hit") is True)
            miss_count = sum(1 for item in group_items if item.get("overall", {}).get("hit") is False)
            count = len(group_items)
            avg_score = round(sum(scores) / len(scores), 4) if scores else None
            hit_rate = round(hit_count / count, 4) if count else None
            rows.append({
                "key": group_key,
                "label": self._group_label(key, group_key),
                "count": count,
                "hit_count": hit_count,
                "miss_count": miss_count,
                "hit_rate": hit_rate,
                "avg_score": avg_score,
                "best_score": round(max(scores), 4) if scores else None,
                "worst_score": round(min(scores), 4) if scores else None,
            })
        return sorted(rows, key=lambda row: (-(row["count"] or 0), -(row["hit_rate"] or 0), row["key"]))

    def _group_by_confidence_bucket(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        bucket_defs = [
            ("low", "低置信 <50%", 0.0, 0.5),
            ("medium", "中置信 50-70%", 0.5, 0.7),
            ("high", "高置信 70-85%", 0.7, 0.85),
            ("very_high", "极高置信 ≥85%", 0.85, 1.01),
            ("unknown", "未知置信度", None, None),
        ]
        bucketed: dict[str, list[dict[str, Any]]] = {key: [] for key, *_ in bucket_defs}
        labels = {key: label for key, label, *_ in bucket_defs}
        order = {key: index for index, (key, *_rest) in enumerate(bucket_defs)}

        for item in items:
            confidence = self._to_float(item.get("confidence"), -1.0)
            target_key = "unknown"
            for key, _label, lower, upper in bucket_defs:
                if lower is None or upper is None:
                    continue
                if lower <= confidence < upper:
                    target_key = key
                    break
            bucketed[target_key].append(item)

        rows = []
        for key, group_items in bucketed.items():
            if not group_items:
                continue
            scores = [float(item["overall"]["score"]) for item in group_items if item.get("overall")]
            hit_count = sum(1 for item in group_items if item.get("overall", {}).get("hit") is True)
            miss_count = sum(1 for item in group_items if item.get("overall", {}).get("hit") is False)
            count = len(group_items)
            avg_score = round(sum(scores) / len(scores), 4) if scores else None
            rows.append({
                "key": key,
                "label": labels[key],
                "count": count,
                "hit_count": hit_count,
                "miss_count": miss_count,
                "hit_rate": round(hit_count / count, 4) if count else None,
                "avg_score": avg_score,
                "best_score": round(max(scores), 4) if scores else None,
                "worst_score": round(min(scores), 4) if scores else None,
            })
        return sorted(rows, key=lambda row: order.get(row["key"], 999))

    def feedback_for_prompt(self, *, symbol: str | None = None, limit: int = 50) -> dict[str, Any]:
        """给 AI 分析 Prompt 注入的轻量复盘反哺，不改变交易动作，只影响模型自我校准。"""
        summary = self.evaluate_records(symbol=symbol, limit=limit)
        feedback = summary.get("feedback") or {}
        return {
            "available": bool(summary.get("evaluated_count")),
            "source": "ai_analysis_evaluation",
            "mode": summary.get("mode"),
            "evaluated_count": summary.get("evaluated_count"),
            "avg_score": summary.get("avg_score"),
            "hit_rate": summary.get("hit_rate"),
            "quality_label": summary.get("quality_label"),
            "prompt_guidance": feedback.get("prompt_guidance") or [],
            "strategy_guidance": feedback.get("strategy_guidance") or [],
            "weak_patterns": feedback.get("weak_patterns") or [],
            "strong_patterns": feedback.get("strong_patterns") or [],
            "disclaimer": "复盘反哺只用于校准 Prompt 和策略阈值，不代表自动交易或保证未来有效。",
        }

    def _feedback_summary(
        self,
        *,
        completed: list[dict[str, Any]],
        avg_score: float | None,
        groups: dict[str, Any],
        risk_constraint_stats: dict[str, Any],
    ) -> dict[str, Any]:
        if not completed:
            return {
                "available": False,
                "summary": "暂无已完成复盘样本，暂不反哺 Prompt 或策略。",
                "prompt_guidance": ["等待至少 3-5 条完成复盘后，再根据评分调整 Prompt 约束。"],
                "strategy_guidance": ["当前不建议调整策略阈值，避免用过少样本过拟合。"],
                "weak_patterns": [],
                "strong_patterns": [],
            }

        action_groups = groups.get("by_action") or []
        confidence_groups = groups.get("by_confidence_bucket") or []
        weak_actions = [item for item in action_groups if (item.get("avg_score") is not None and item.get("avg_score") < 0.58 and item.get("count", 0) >= 1)]
        strong_actions = [item for item in action_groups if (item.get("avg_score") is not None and item.get("avg_score") >= 0.72 and item.get("count", 0) >= 1)]
        high_confidence_weak = [item for item in confidence_groups if item.get("key") in {"high", "very_high"} and item.get("avg_score") is not None and item.get("avg_score") < 0.58]
        weak_patterns = self._weak_pattern_rows(completed)
        strong_patterns = self._strong_pattern_rows(completed)
        prompt_guidance = self._prompt_guidance(avg_score, weak_actions, high_confidence_weak, weak_patterns, risk_constraint_stats)
        strategy_guidance = self._strategy_guidance(weak_actions, strong_actions, weak_patterns, risk_constraint_stats)
        return {
            "available": True,
            "summary": self._feedback_summary_text(avg_score, weak_actions, high_confidence_weak, weak_patterns),
            "prompt_guidance": prompt_guidance,
            "strategy_guidance": strategy_guidance,
            "weak_patterns": weak_patterns,
            "strong_patterns": strong_patterns,
        }

    def _weak_pattern_rows(self, completed: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows = []
        for item in sorted(completed, key=lambda row: float(row.get("overall", {}).get("score") or 0))[:5]:
            overall = item.get("overall") or {}
            if float(overall.get("score") or 0) >= 0.58:
                continue
            worst = self._worst_horizon(item.get("horizons") or {})
            rows.append({
                "analysis_id": item.get("analysis_id"),
                "symbol": item.get("symbol"),
                "action": item.get("action"),
                "score": overall.get("score"),
                "grade": overall.get("grade"),
                "miss_type": worst.get("verdict") or "miss",
                "reason": worst.get("reason") or overall.get("reason"),
                "prompt_fix": self._prompt_fix_for_miss(item.get("action"), worst.get("verdict")),
                "strategy_fix": self._strategy_fix_for_miss(item.get("action"), worst.get("verdict")),
            })
        return rows

    def _strong_pattern_rows(self, completed: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows = []
        for item in sorted(completed, key=lambda row: float(row.get("overall", {}).get("score") or 0), reverse=True)[:5]:
            overall = item.get("overall") or {}
            if float(overall.get("score") or 0) < 0.72:
                continue
            rows.append({
                "analysis_id": item.get("analysis_id"),
                "symbol": item.get("symbol"),
                "action": item.get("action"),
                "score": overall.get("score"),
                "grade": overall.get("grade"),
                "reason": overall.get("reason"),
                "reuse_hint": "保留这类信号组合，但仍需继续累计样本，避免短期过拟合。",
            })
        return rows

    def _prompt_guidance(
        self,
        avg_score: float | None,
        weak_actions: list[dict[str, Any]],
        high_confidence_weak: list[dict[str, Any]],
        weak_patterns: list[dict[str, Any]],
        risk_constraint_stats: dict[str, Any],
    ) -> list[str]:
        guidance: list[str] = []
        if avg_score is not None and avg_score < 0.58:
            guidance.append("整体复盘均分偏低：Prompt 应要求模型更明确地区分买入、观察、规避，不要用模糊结论替代触发条件。")
        if high_confidence_weak:
            guidance.append("高置信区间复盘偏弱：下调高置信输出门槛，要求至少趋势、量能、消息面、风控四类证据中三类同向才允许 confidence ≥ 0.70。")
        if any(item.get("key") == "buy" for item in weak_actions):
            guidance.append("buy 建议复盘偏弱：Prompt 应强化 entry_condition、stop_loss、invalid_condition，缺任一项时不得输出 buy。")
        if any(item.get("key") in {"avoid", "reduce", "sell"} for item in weak_actions):
            guidance.append("规避/减仓/卖出类复盘偏弱：Prompt 应检查是否过度保守，要求说明机会成本和重新转入观察的条件。")
        if any(item.get("key") == "watch" for item in weak_actions):
            guidance.append("watch 结论复盘偏弱：Prompt 应要求给出触发买入/风险复核的具体价格、量能或交易日条件。")
        if weak_patterns:
            guidance.extend([str(item.get("prompt_fix")) for item in weak_patterns[:2] if item.get("prompt_fix")])
        if (risk_constraint_stats.get("avg_score") is not None and risk_constraint_stats.get("avg_score") < 0.58):
            guidance.append("风控硬约束覆盖后的复盘偏弱：Prompt 应要求模型解释原始建议和风控覆盖之间的冲突，避免机械套用结论。")
        return self._dedupe(guidance) or ["当前复盘表现可作为参考，继续累计样本后再做更强 Prompt 约束。"]

    def _strategy_guidance(
        self,
        weak_actions: list[dict[str, Any]],
        strong_actions: list[dict[str, Any]],
        weak_patterns: list[dict[str, Any]],
        risk_constraint_stats: dict[str, Any],
    ) -> list[str]:
        guidance: list[str] = []
        if any(item.get("key") == "buy" for item in weak_actions):
            guidance.append("买入策略建议收紧：提高观察池触发阈值，要求涨幅、量能或 RAG 利好至少两个条件同时成立后再进入买入复查。")
        if any(item.get("key") == "watch" for item in weak_actions):
            guidance.append("观察策略建议结构化：watch 必须产生明确的 next_check_at、触发价和失效条件，否则只保留在低优先级观察区。")
        if any(item.get("key") in {"avoid", "reduce", "sell"} for item in weak_actions):
            guidance.append("规避/减仓策略建议校准：如果后续经常错过上行，降低单一风险信号权重，增加板块强度和量能恢复条件。")
        if strong_actions:
            labels = "、".join(str(item.get("label")) for item in strong_actions[:3])
            guidance.append(f"高分动作可保留为策略正样本：{labels}，但需要继续观察样本数量是否足够。")
        if weak_patterns:
            guidance.extend([str(item.get("strategy_fix")) for item in weak_patterns[:2] if item.get("strategy_fix")])
        if risk_constraint_stats.get("evaluated_triggered_count") and risk_constraint_stats.get("hit_rate") is not None:
            if risk_constraint_stats.get("hit_rate") >= 0.58:
                guidance.append("风控硬约束覆盖目前有效：继续保持固定止损止盈优先级。")
            else:
                guidance.append("风控硬约束覆盖效果偏弱：先检查样本和价格同步，不建议立即放宽止损止盈纪律。")
        return self._dedupe(guidance) or ["策略层暂不建议大改，先继续累计复盘样本。"]

    def _feedback_summary_text(
        self,
        avg_score: float | None,
        weak_actions: list[dict[str, Any]],
        high_confidence_weak: list[dict[str, Any]],
        weak_patterns: list[dict[str, Any]],
    ) -> str:
        if avg_score is None:
            return "复盘样本存在，但均分不可用，暂只输出轻量建议。"
        parts = [f"复盘均分 {avg_score:.2f}"]
        if weak_actions:
            parts.append("低分动作：" + "、".join(str(item.get("label")) for item in weak_actions[:3]))
        if high_confidence_weak:
            parts.append("高置信样本需要校准")
        if weak_patterns:
            parts.append("已提取可反哺的低分样本")
        return "；".join(parts) + "。"

    def _worst_horizon(self, horizons: dict[str, Any]) -> dict[str, Any]:
        evaluated = [item for item in horizons.values() if isinstance(item, dict) and item.get("status") == "evaluated"]
        if not evaluated:
            return {}
        return min(evaluated, key=lambda item: float(item.get("score") or 0))

    def _prompt_fix_for_miss(self, action: str | None, miss_type: str | None) -> str:
        if miss_type == "risk_miss" or action in {"buy", "hold"}:
            return "反哺 Prompt：看多结论必须先说明最大可承受回撤和失效条件，短线回撤风险不清楚时降级为 watch。"
        if miss_type == "opportunity_miss" or action in {"avoid", "reduce", "sell"}:
            return "反哺 Prompt：看空/规避结论必须评估机会成本，若板块或量能仍强，需要给出重新观察条件。"
        if miss_type == "action_too_conservative" or action == "watch":
            return "反哺 Prompt：观察结论必须输出可执行触发条件，不能只写继续观察。"
        return "反哺 Prompt：要求模型解释低分窗口的方向、回撤、机会成本和置信度问题。"

    def _strategy_fix_for_miss(self, action: str | None, miss_type: str | None) -> str:
        if miss_type == "risk_miss" or action in {"buy", "hold"}:
            return "策略反哺：买入/持有信号增加回撤保护，触发价附近若量能不足或跌破短均线则延后。"
        if miss_type == "opportunity_miss" or action in {"avoid", "reduce", "sell"}:
            return "策略反哺：规避信号增加机会成本检查，板块强势或放量修复时不要直接关闭观察。"
        if miss_type == "action_too_conservative" or action == "watch":
            return "策略反哺：观察池需要把 watch 拆成触发买入、触发风险、继续低优先级三类状态。"
        return "策略反哺：优先调整触发条件和复查周期，不直接改自动交易规则。"

    def _dedupe(self, values: list[str]) -> list[str]:
        seen = set()
        rows = []
        for value in values:
            if not value or value in seen:
                continue
            seen.add(value)
            rows.append(value)
        return rows

    def _risk_constraint_stats(self, items: list[dict[str, Any]], completed: list[dict[str, Any]]) -> dict[str, Any]:
        triggered_items = [item for item in items if item.get("risk_constraint_triggered")]
        evaluated_triggered = [item for item in completed if item.get("risk_constraint_triggered")]
        hit_items = [item for item in evaluated_triggered if item.get("overall", {}).get("hit") is True]
        miss_items = [item for item in evaluated_triggered if item.get("overall", {}).get("hit") is False]
        scores = [float(item["overall"]["score"]) for item in evaluated_triggered if item.get("overall")]
        return {
            "triggered_count": len(triggered_items),
            "triggered_rate": round(len(triggered_items) / len(items), 4) if items else None,
            "evaluated_triggered_count": len(evaluated_triggered),
            "hit_count": len(hit_items),
            "miss_count": len(miss_items),
            "hit_rate": round(len(hit_items) / len(evaluated_triggered), 4) if evaluated_triggered else None,
            "avg_score": round(sum(scores) / len(scores), 4) if scores else None,
            "by_original_action": self._group_by(evaluated_triggered, "risk_original_action"),
            "by_forced_action": self._group_by(evaluated_triggered, "risk_forced_action"),
        }

    def _group_label(self, group: str, key: str) -> str:
        labels = {
            "analysis_type": {
                "buy_decision": "买入决策",
                "position_review": "持仓复盘",
                "risk_review": "风险审查",
                "unknown": "未知类型",
            },
            "action": {
                "buy": "建议买入",
                "watch": "继续观察",
                "avoid": "暂不参与",
                "hold": "继续持有",
                "reduce": "建议减仓",
                "sell": "建议卖出",
                "unknown": "未知动作",
            },
            "risk_level": {
                "low": "低风险",
                "medium": "中风险",
                "high": "高风险",
                "unknown": "未知风险",
            },
            "risk_original_action": {
                "buy": "原始买入",
                "watch": "原始观察",
                "avoid": "原始规避",
                "hold": "原始持有",
                "reduce": "原始减仓",
                "sell": "原始卖出",
                "unknown": "未知原始建议",
            },
            "risk_forced_action": {
                "reduce": "强制减仓",
                "sell": "强制卖出",
                "unknown": "未知强制动作",
            },
        }
        return labels.get(group, {}).get(key, key)

    def _load_records(self, *, symbol: str | None, limit: int) -> list[AIAnalysisRecordModel]:
        stmt = (
            select(AIAnalysisRecordModel)
            .where(AIAnalysisRecordModel.status == "success")
            .order_by(AIAnalysisRecordModel.created_at.desc())
            .limit(limit)
        )
        if symbol:
            stmt = (
                select(AIAnalysisRecordModel)
                .where(
                    AIAnalysisRecordModel.status == "success",
                    AIAnalysisRecordModel.symbol == self._normalize_symbol(symbol),
                )
                .order_by(AIAnalysisRecordModel.created_at.desc())
                .limit(limit)
            )
        with SessionLocal() as session:
            return session.scalars(stmt).all()

    def _find_anchor_index(self, klines: list[dict[str, Any]], created_at: str) -> int | None:
        if not klines:
            return None
        created_date = self._date_key(created_at)
        for index, row in enumerate(klines):
            if self._date_key(str(row.get("trade_time") or "")) >= created_date:
                return index
        return len(klines) - 1 if klines else None

    def _history_anchor_indexes(self, klines: list[dict[str, Any]], *, max_horizon: int, max_samples: int) -> list[int]:
        latest_allowed = len(klines) - max_horizon - 1
        if latest_allowed < 0:
            return []
        lookback_start = max(0, latest_allowed - 120)
        if max_samples <= 1:
            return [latest_allowed]
        span = latest_allowed - lookback_start
        if span <= 0:
            return [latest_allowed]
        step = max(1, span // max(1, max_samples - 1))
        candidates = [latest_allowed - step * index for index in range(max_samples)]
        return sorted({index for index in candidates if lookback_start <= index <= latest_allowed})

    def _daily_returns(self, klines: list[dict[str, Any]]) -> list[float]:
        returns = []
        for previous, current in zip(klines, klines[1:]):
            prev_close = self._to_float(previous.get("close"), 0.0)
            curr_close = self._to_float(current.get("close"), 0.0)
            if prev_close > 0 and curr_close > 0:
                returns.append((curr_close - prev_close) / prev_close)
        return returns

    def _record_base(self, row: AIAnalysisRecordModel) -> dict[str, Any]:
        output = self._loads(row.output_json)
        return {
            "analysis_id": row.analysis_id,
            "symbol": row.symbol,
            "analysis_type": row.analysis_type,
            "action": row.action,
            "confidence": row.confidence,
            "risk_level": row.risk_level,
            "risk_constraint_triggered": bool(getattr(row, "risk_constraint_triggered", False)),
            "risk_forced_action": getattr(row, "risk_forced_action", None),
            "risk_original_action": getattr(row, "risk_original_action", None),
            "risk_trigger_message": getattr(row, "risk_trigger_message", None),
            "risk_original_confidence": getattr(row, "risk_original_confidence", None),
            "risk_final_confidence": getattr(row, "risk_final_confidence", None),
            "risk_constraint": self._loads(getattr(row, "risk_constraint_json", None)),
            "summary": output.get("summary") if isinstance(output, dict) else None,
            "created_at": row.created_at,
            "model_provider": row.model_provider,
            "model_name": row.model_name,
        }

    def _kline_brief(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "trade_time": row.get("trade_time"),
            "open": row.get("open"),
            "high": row.get("high"),
            "low": row.get("low"),
            "close": row.get("close"),
        }

    def _overall_summary(self, action: str | None, score: float, hit: bool) -> str:
        verdict = "命中" if hit else "未命中"
        return f"{action or '-'} 建议的风险收益综合评分为 {score:.2f}，判定为{verdict}。"

    def _grade(self, score: float) -> str:
        if score >= 0.75:
            return "A"
        if score >= 0.58:
            return "B"
        if score >= 0.45:
            return "C"
        return "D"

    def _date_key(self, value: str) -> str:
        if not value:
            return "0000-00-00"
        text = value[:10]
        if len(text) == 8 and text.isdigit():
            return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
        except Exception:
            return text

    def _loads(self, value: str | None) -> Any:
        if not value:
            return {}
        try:
            return json.loads(value)
        except Exception:
            return {}

    def _ratio_to_pct(self, value: float) -> float:
        return round(value * 100, 4)

    def _to_float(self, value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _clamp(self, value: float, lower: float = 0.0, upper: float = 1.0) -> float:
        return max(lower, min(upper, value))

    def _normalize_symbol(self, symbol: Any) -> str:
        text = str(symbol or "").strip().upper()
        if "." in text:
            text = text.split(".")[0]
        return text.zfill(6) if text.isdigit() and len(text) < 6 else text
