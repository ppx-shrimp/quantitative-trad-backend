from __future__ import annotations

from typing import Any

from quant_system.services.feature_service import FeatureService
from quant_system.services.stock_pool_service import StockPoolService


class AIRecommendationService:
    """股票池内 AI 候选推荐。

    第一版定位为“稳健观察”：只从已有股票池中筛选，不自动下单；
    输出适合进一步 AI 分析/人工观察的候选，而不是直接买入指令。
    """

    def __init__(self) -> None:
        self.stock_pool_service = StockPoolService()
        self.feature_service = FeatureService()

    def recommend_from_pool(
        self,
        *,
        pool_code: str = "favorites",
        limit: int = 5,
        period: str = "daily",
        style: str = "steady_watch",
    ) -> dict[str, Any]:
        members = self.stock_pool_service.list_members(pool_code)
        stock_members = [member for member in members if self._is_stock_symbol(member.get("symbol", ""))]
        candidates: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []

        for member in stock_members:
            symbol = member["symbol"]
            analysis = self.feature_service.analyze_symbol(symbol, period=period)
            if analysis.get("status") != "ok":
                skipped.append({"symbol": symbol, "name": member.get("name"), "reason": analysis.get("summary")})
                continue
            item = self._score_candidate(member=member, analysis=analysis, style=style)
            candidates.append(item)

        ranked = sorted(candidates, key=lambda item: item["recommendation_score"], reverse=True)
        return {
            "mode": "pool_steady_watch",
            "pool_code": pool_code,
            "period": period,
            "style": style,
            "count": len(ranked[:limit]),
            "total_candidates": len(candidates),
            "skipped_count": len(skipped),
            "items": ranked[:limit],
            "skipped": skipped[:10],
            "disclaimer": "推荐结果仅用于观察和进一步分析，不构成自动买入指令；下单仍需手动确认并遵守固定风控规则。",
        }

    def _score_candidate(self, *, member: dict[str, Any], analysis: dict[str, Any], style: str) -> dict[str, Any]:
        latest = analysis.get("latest_feature") or {}
        scores = analysis.get("scores") or {}
        trend_score = float(scores.get("trend") or latest.get("trend_score") or 50)
        technical = float(scores.get("technical") or 50)
        momentum = float(scores.get("momentum") or 50)
        risk = float(scores.get("risk") or 50)
        return_5 = self._to_float(latest.get("return_5")) or 0.0
        return_20 = self._to_float(latest.get("return_20")) or 0.0
        volatility = self._to_float(latest.get("volatility_20")) or 0.0
        price_position_20 = self._to_float(latest.get("price_position_20"))
        price_position_60 = self._to_float(latest.get("price_position_60"))

        score = 0.0
        score += technical * 0.24
        score += trend_score * 0.24
        score += risk * 0.28
        score += momentum * 0.12
        score += self._entry_position_score(price_position_20, price_position_60) * 0.12

        penalties: list[str] = []
        highlights: list[str] = []
        if volatility > 0.06:
            score -= 12
            penalties.append("20 日波动偏高，暂不适合重仓")
        elif volatility <= 0.035:
            highlights.append("波动率相对可控")

        if return_5 > 0.12:
            score -= 8
            penalties.append("近 5 日涨幅偏大，追高风险上升")
        elif -0.03 <= return_5 <= 0.06:
            highlights.append("近 5 日涨跌幅处于较温和区间")

        if latest.get("trend_direction") in {"strong_down", "down"}:
            score -= 16
            penalties.append("均线结构偏弱，先观察")
        elif latest.get("trend_direction") in {"strong_up", "up"}:
            highlights.append("均线结构偏多")

        if latest.get("signal") == "bullish":
            highlights.append("规则特征信号偏多")
        elif latest.get("signal") == "bearish":
            score -= 10
            penalties.append("规则特征信号偏空")

        score = round(max(0, min(100, score)), 2)
        action = self._recommendation_action(score=score, penalties=penalties, latest=latest)
        confidence = round(max(0.2, min(0.82, score / 100)), 4)
        reasons = highlights[:3] or [analysis.get("summary") or "技术特征中性，建议继续观察"]
        risk_notes = penalties[:3] or ["未发现明显技术面硬伤，但仍需结合新闻、资金面和固定风控线复核"]
        reason_evidence = self._reason_evidence(
            reasons=reasons,
            risk_notes=risk_notes,
            latest=latest,
            scores=scores,
            trend_score=trend_score,
            technical=technical,
            momentum=momentum,
            risk=risk,
            volatility=volatility,
            return_5=return_5,
            return_20=return_20,
            price_position_20=price_position_20,
            price_position_60=price_position_60,
        )
        confidence_tier = self._confidence_tier(confidence=confidence, reason_evidence=reason_evidence)

        return {
            "symbol": member.get("symbol"),
            "name": member.get("name") or member.get("symbol"),
            "pool_code": member.get("pool_code"),
            "recommendation_score": score,
            "action": action,
            "confidence": confidence,
            "confidence_tier": confidence_tier,
            "risk_level": self._risk_level(risk=risk, volatility=volatility, score=score),
            "summary": self._summary(action=action, score=score, latest=latest),
            "reasons": reasons,
            "risk_notes": risk_notes,
            "reason_evidence": reason_evidence,
            "suggested_next_step": "建议先点击 AI 分析做单票深度分析；若仍为买入/观察且风险可控，再手动带入交易面板小仓位确认。",
            "latest_feature": latest,
            "scores": scores,
            "feature_summary": analysis.get("summary"),
        }

    def _reason_evidence(
        self,
        *,
        reasons: list[str],
        risk_notes: list[str],
        latest: dict[str, Any],
        scores: dict[str, Any],
        trend_score: float,
        technical: float,
        momentum: float,
        risk: float,
        volatility: float,
        return_5: float,
        return_20: float,
        price_position_20: float | None,
        price_position_60: float | None,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        score_map = {
            "均线结构偏多": (trend_score, "trend", "趋势分"),
            "规则特征信号偏多": (technical, "technical", "技术分"),
            "波动率相对可控": (risk, "risk", "风险分"),
            "近 5 日涨跌幅处于较温和区间": (momentum, "momentum", "动量分"),
        }
        for reason in reasons:
            matched = next((value for key, value in score_map.items() if key in reason), None)
            if matched:
                evidence_score, dimension, dimension_label = matched
            else:
                evidence_score, dimension, dimension_label = self._fallback_reason_score(reason, scores), "summary", "综合特征"
            rows.append({
                "text": reason,
                "kind": "positive",
                "dimension": dimension,
                "dimension_label": dimension_label,
                "confidence": self._evidence_confidence(evidence_score),
                "tier": self._evidence_tier(evidence_score),
                "evidence_score": round(float(evidence_score), 2),
                "explanation": self._evidence_explanation(dimension=dimension, score=evidence_score, latest=latest),
                "metrics": self._reason_metrics(
                    dimension=dimension,
                    latest=latest,
                    volatility=volatility,
                    return_5=return_5,
                    return_20=return_20,
                    price_position_20=price_position_20,
                    price_position_60=price_position_60,
                ),
            })
        for note in risk_notes:
            risk_score = self._risk_note_score(note=note, risk=risk, volatility=volatility, return_5=return_5, latest=latest)
            rows.append({
                "text": note,
                "kind": "risk",
                "dimension": "risk",
                "dimension_label": "风险约束",
                "confidence": self._evidence_confidence(risk_score),
                "tier": self._evidence_tier(risk_score),
                "evidence_score": round(float(risk_score), 2),
                "explanation": self._risk_evidence_explanation(note=note, score=risk_score),
                "metrics": self._reason_metrics(
                    dimension="risk",
                    latest=latest,
                    volatility=volatility,
                    return_5=return_5,
                    return_20=return_20,
                    price_position_20=price_position_20,
                    price_position_60=price_position_60,
                ),
            })
        return rows

    def _confidence_tier(self, *, confidence: float, reason_evidence: list[dict[str, Any]]) -> dict[str, Any]:
        scores = [float(item.get("evidence_score") or 0) for item in reason_evidence]
        avg_evidence = sum(scores) / len(scores) if scores else confidence * 100
        strong_count = sum(1 for item in reason_evidence if item.get("tier") == "strong")
        weak_count = sum(1 for item in reason_evidence if item.get("tier") == "weak")
        composite = avg_evidence * 0.65 + confidence * 100 * 0.35
        if composite >= 72 and weak_count == 0:
            tier = "strong"
            label = "高可信"
            explanation = "推荐理由有较明确的技术证据支撑，可进入优先复查，但仍不是买入指令。"
        elif composite >= 58:
            tier = "medium"
            label = "中可信"
            explanation = "推荐理由有部分证据支撑，需要继续用单股 AI 分析和新闻/RAG 证据确认。"
        else:
            tier = "weak"
            label = "低可信"
            explanation = "推荐理由证据偏弱，建议只低优先级观察或暂不参与。"
        return {
            "tier": tier,
            "label": label,
            "score": round(composite, 2),
            "avg_evidence_score": round(avg_evidence, 2),
            "strong_reason_count": strong_count,
            "weak_reason_count": weak_count,
            "explanation": explanation,
        }

    def _fallback_reason_score(self, reason: str, scores: dict[str, Any]) -> float:
        values = [self._to_float(value) for value in scores.values()]
        numeric = [value for value in values if value is not None]
        if numeric:
            return sum(numeric) / len(numeric)
        return 52.0 if reason else 45.0

    def _risk_note_score(self, *, note: str, risk: float, volatility: float, return_5: float, latest: dict[str, Any]) -> float:
        if "硬伤" in note or "未发现" in note:
            return max(52.0, risk)
        score = 72.0
        if volatility > 0.06:
            score += 8
        if return_5 > 0.12:
            score += 6
        if latest.get("trend_direction") in {"strong_down", "down"}:
            score += 10
        if latest.get("signal") == "bearish":
            score += 8
        return min(95.0, score)

    def _evidence_confidence(self, score: float) -> float:
        return round(max(0.2, min(0.9, score / 100)), 4)

    def _evidence_tier(self, score: float) -> str:
        if score >= 72:
            return "strong"
        if score >= 58:
            return "medium"
        return "weak"

    def _evidence_explanation(self, *, dimension: str, score: float, latest: dict[str, Any]) -> str:
        if dimension == "trend":
            return f"趋势证据来自均线/趋势特征，当前趋势={latest.get('trend_direction') or '-'}，分值 {score:.1f}。"
        if dimension == "technical":
            return f"技术证据来自规则信号和技术评分，当前信号={latest.get('signal') or '-'}，分值 {score:.1f}。"
        if dimension == "momentum":
            return f"动量证据来自短期涨跌幅和 momentum 分，分值 {score:.1f}。"
        if dimension == "risk":
            return f"风险证据来自波动率和风险评分，风险分 {score:.1f}。"
        return f"综合特征支撑强度 {score:.1f}，需要结合单股 AI 分析继续确认。"

    def _risk_evidence_explanation(self, *, note: str, score: float) -> str:
        if score >= 72:
            return f"该风险提示证据较强：{note}。需要优先进入风险复核。"
        if score >= 58:
            return f"该风险提示有一定依据：{note}。建议在单股复查时重点确认。"
        return f"该风险提示证据偏弱：{note}。保留为提醒，不单独作为决策依据。"

    def _reason_metrics(
        self,
        *,
        dimension: str,
        latest: dict[str, Any],
        volatility: float,
        return_5: float,
        return_20: float,
        price_position_20: float | None,
        price_position_60: float | None,
    ) -> dict[str, Any]:
        metrics = {
            "trend_direction": latest.get("trend_direction"),
            "signal": latest.get("signal"),
            "return_5": return_5,
            "return_20": return_20,
            "volatility_20": volatility,
        }
        if dimension in {"trend", "summary"}:
            metrics.update({"price_position_20": price_position_20, "price_position_60": price_position_60})
        return metrics

    def _entry_position_score(self, price_position_20: float | None, price_position_60: float | None) -> float:
        values = [value for value in [price_position_20, price_position_60] if value is not None]
        if not values:
            return 50.0
        avg = sum(values) / len(values)
        if 0.35 <= avg <= 0.72:
            return 82.0
        if 0.2 <= avg < 0.35:
            return 64.0
        if 0.72 < avg <= 0.88:
            return 58.0
        return 42.0

    def _recommendation_action(self, *, score: float, penalties: list[str], latest: dict[str, Any]) -> str:
        if score >= 72 and latest.get("trend_direction") not in {"strong_down", "down"} and len(penalties) <= 1:
            return "watch_first"
        if score >= 62:
            return "observe"
        return "avoid_now"

    def _risk_level(self, *, risk: float, volatility: float, score: float) -> str:
        if volatility > 0.07 or risk < 45 or score < 55:
            return "high"
        if volatility > 0.045 or risk < 65:
            return "medium"
        return "low"

    def _summary(self, *, action: str, score: float, latest: dict[str, Any]) -> str:
        action_text = {
            "watch_first": "优先观察候选",
            "observe": "继续观察",
            "avoid_now": "暂不参与",
        }.get(action, action)
        return (
            f"{action_text}，综合分 {score}。"
            f"趋势={latest.get('trend_direction') or '-'}，信号={latest.get('signal') or '-'}，"
            f"20日波动={self._pct(latest.get('volatility_20'))}。"
        )

    def _pct(self, value: Any) -> str:
        number = self._to_float(value)
        if number is None:
            return "未知"
        return f"{number * 100:.2f}%"

    def _to_float(self, value: Any) -> float | None:
        if value in (None, "", "-"):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _is_stock_symbol(self, symbol: str) -> bool:
        normalized = str(symbol or "").strip().upper().split(".")[0]
        return len(normalized) == 6 and normalized.isdigit() and not normalized.startswith("88")
