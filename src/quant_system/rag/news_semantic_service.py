from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import select

from quant_system.ai.llm_client import OpenAICompatibleClient
from quant_system.db.database import SessionLocal, init_sqlalchemy_tables
from quant_system.db.models import MarketNewsModel, NewsRAGChunkModel


class NewsSemanticService:
    """Rule + optional LLM semantic analysis for market news."""

    SECTOR_KEYWORDS: dict[str, list[str]] = {
        "光模块设备": ["光模块", "CPO", "800G", "1.6T", "硅光", "光通信", "光芯片", "光器件", "高速光模块"],
        "算力基础设施": ["算力", "AI服务器", "GPU", "英伟达", "数据中心", "液冷", "智算中心", "服务器", "算力租赁"],
        "半导体芯片": ["半导体", "芯片", "晶圆", "封测", "先进封装", "HBM", "存储芯片", "EDA", "光刻胶", "设备国产化"],
        "电力电网": ["电力", "电网", "特高压", "虚拟电厂", "储能", "新能源消纳", "变压器", "配电", "电力设备"],
        "新能源车": ["新能源车", "汽车", "锂电", "动力电池", "固态电池", "充电桩", "智能驾驶", "车载", "零部件"],
        "机器人": ["机器人", "人形机器人", "减速器", "伺服", "灵巧手", "执行器", "关节模组", "工业机器人"],
        "低空经济": ["低空经济", "eVTOL", "无人机", "飞行汽车", "通航", "空域", "低空飞行"],
        "医药医疗": ["医药", "创新药", "医疗器械", "CXO", "疫苗", "中药", "集采", "临床", "生物医药"],
        "消费零售": ["消费", "零售", "白酒", "食品饮料", "旅游", "酒店", "免税", "家电", "餐饮"],
        "金融地产": ["银行", "券商", "保险", "地产", "房地产", "降息", "按揭", "融资融券", "资本市场"],
        "军工航空": ["军工", "航空", "航天", "卫星", "北斗", "导弹", "大飞机", "商业航天"],
        "游戏传媒": ["游戏", "传媒", "影视", "短剧", "广告", "AIGC", "IP", "出版", "版权"],
    }

    POSITIVE_KEYWORDS = ["增长", "利好", "突破", "中标", "订单", "涨价", "扩产", "政策支持", "补贴", "回购", "增持", "超预期", "放量", "改善", "创新高", "合作"]
    NEGATIVE_KEYWORDS = ["下滑", "利空", "亏损", "减持", "处罚", "调查", "终止", "延期", "低于预期", "违约", "风险", "下调", "跌价", "需求疲软", "裁员"]

    def __init__(self) -> None:
        init_sqlalchemy_tables()
        self.llm_client = OpenAICompatibleClient()

    def analyze_text(self, *, text: str, use_llm: bool = True) -> dict[str, Any]:
        clean_text = self._clean_text(text)
        if not clean_text:
            return {"ok": False, "error": "text 不能为空"}
        rule_result = self._rule_analyze(clean_text)
        llm_result = self._llm_analyze(clean_text, rule_result) if use_llm and self.llm_client.enabled() else None
        return self._merge_result(rule_result, llm_result, source={"type": "text"}, llm_used=bool(llm_result))

    def analyze_news(self, *, news_id: str, use_llm: bool = True) -> dict[str, Any]:
        with SessionLocal() as session:
            row = session.scalar(select(MarketNewsModel).where(MarketNewsModel.news_id == news_id))
        if row is None:
            return {"ok": False, "error": f"未找到新闻：{news_id}"}
        text = "\n".join(part for part in [row.title, row.summary, row.content] if part)
        rule_result = self._rule_analyze(text)
        llm_result = self._llm_analyze(text, rule_result) if use_llm and self.llm_client.enabled() else None
        return self._merge_result(
            rule_result,
            llm_result,
            source={
                "type": "market_news",
                "news_id": row.news_id,
                "title": row.title,
                "source": row.source,
                "published_at": row.published_at,
                "existing_related_symbols": self._json_list(row.related_symbols),
                "existing_related_sectors": self._json_list(row.related_sectors),
                "existing_tags": self._json_list(row.tags),
            },
            llm_used=bool(llm_result),
        )

    def analyze_chunk(self, *, chunk_id: str, use_llm: bool = True) -> dict[str, Any]:
        with SessionLocal() as session:
            row = session.scalar(select(NewsRAGChunkModel).where(NewsRAGChunkModel.chunk_id == chunk_id))
        if row is None:
            return {"ok": False, "error": f"未找到 chunk：{chunk_id}"}
        rule_result = self._rule_analyze(row.text)
        llm_result = self._llm_analyze(row.text, rule_result) if use_llm and self.llm_client.enabled() else None
        metadata = self._json_object(row.metadata_json)
        return self._merge_result(
            rule_result,
            llm_result,
            source={
                "type": "news_rag_chunk",
                "chunk_id": row.chunk_id,
                "news_id": row.news_id,
                "chunk_index": row.chunk_index,
                "title": metadata.get("title"),
                "source": metadata.get("source"),
                "published_at": metadata.get("published_at"),
            },
            llm_used=bool(llm_result),
        )

    def _rule_analyze(self, text: str) -> dict[str, Any]:
        sector_scores = []
        lower_text = text.lower()
        for sector, keywords in self.SECTOR_KEYWORDS.items():
            matched = [keyword for keyword in keywords if keyword.lower() in lower_text]
            if matched:
                score = min(1.0, 0.35 + 0.15 * len(matched))
                sector_scores.append({"sector": sector, "score": round(score, 3), "matched_keywords": matched})
        sector_scores.sort(key=lambda item: item["score"], reverse=True)

        positive_hits = [keyword for keyword in self.POSITIVE_KEYWORDS if keyword in text]
        negative_hits = [keyword for keyword in self.NEGATIVE_KEYWORDS if keyword in text]
        sentiment_score = len(positive_hits) - len(negative_hits)
        if sentiment_score > 0:
            sentiment = "positive"
        elif sentiment_score < 0:
            sentiment = "negative"
        else:
            sentiment = "neutral"
        confidence = min(0.9, 0.45 + 0.08 * (len(positive_hits) + len(negative_hits) + len(sector_scores)))
        return {
            "mode": "rule",
            "sectors": sector_scores[:5],
            "primary_sector": sector_scores[0]["sector"] if sector_scores else None,
            "sentiment": sentiment,
            "impact": sentiment,
            "confidence": round(confidence, 3),
            "positive_keywords": positive_hits,
            "negative_keywords": negative_hits,
            "summary": self._rule_summary(sector_scores, sentiment, positive_hits, negative_hits),
            "risk_warnings": self._risk_warnings(text),
        }

    def _llm_analyze(self, text: str, rule_result: dict[str, Any]) -> dict[str, Any] | None:
        prompt = f"""
你是A股新闻语义分析助手。请基于新闻内容和规则初判，输出严格 JSON，不要输出 Markdown。

任务：
1. 判断新闻所属细分板块，可多选，但最多 5 个。
2. 判断对相关板块/股票的影响：positive、negative、neutral、mixed 四选一。
3. 给出置信度 0-1。
4. 给出一句用户可读摘要。
5. 给出风险提示。
6. 可识别相关股票代码或名称时放入 related_symbols，否则空数组。

规则初判：
{json.dumps(rule_result, ensure_ascii=False)}

新闻内容：
{text[:3500]}

请返回 JSON 格式：
{{
  "primary_sector": "光模块设备",
  "sectors": [{{"sector":"光模块设备","score":0.86,"reason":"..."}}],
  "impact": "positive",
  "sentiment": "positive",
  "confidence": 0.82,
  "summary": "...",
  "risk_warnings": ["..."],
  "related_symbols": ["..."]
}}
""".strip()
        try:
            result = self.llm_client.complete_json(prompt, analysis_type="news_semantic")
            return result if isinstance(result, dict) else None
        except Exception as exc:
            return {"error": str(exc)}

    def _merge_result(self, rule_result: dict[str, Any], llm_result: dict[str, Any] | None, *, source: dict[str, Any], llm_used: bool) -> dict[str, Any]:
        if llm_result and not llm_result.get("error"):
            primary_sector = llm_result.get("primary_sector") or rule_result.get("primary_sector")
            sectors = llm_result.get("sectors") or rule_result.get("sectors")
            impact = llm_result.get("impact") or rule_result.get("impact")
            sentiment = llm_result.get("sentiment") or impact
            confidence = self._safe_float(llm_result.get("confidence"), rule_result.get("confidence"))
            summary = llm_result.get("summary") or rule_result.get("summary")
            risk_warnings = llm_result.get("risk_warnings") or rule_result.get("risk_warnings")
            related_symbols = llm_result.get("related_symbols") or []
        else:
            primary_sector = rule_result.get("primary_sector")
            sectors = rule_result.get("sectors")
            impact = rule_result.get("impact")
            sentiment = rule_result.get("sentiment")
            confidence = rule_result.get("confidence")
            summary = rule_result.get("summary")
            risk_warnings = rule_result.get("risk_warnings")
            related_symbols = []
        return {
            "ok": True,
            "source": source,
            "primary_sector": primary_sector,
            "sectors": sectors or [],
            "impact": impact,
            "sentiment": sentiment,
            "confidence": confidence,
            "summary": summary,
            "risk_warnings": risk_warnings or [],
            "related_symbols": related_symbols,
            "rule_result": rule_result,
            "llm": {
                "used": llm_used and bool(llm_result) and not llm_result.get("error"),
                "available": self.llm_client.enabled(),
                "error": llm_result.get("error") if llm_result else None,
            },
        }

    def _rule_summary(self, sectors: list[dict[str, Any]], sentiment: str, positive_hits: list[str], negative_hits: list[str]) -> str:
        sector_text = "、".join(item["sector"] for item in sectors[:3]) or "暂未匹配明确板块"
        if sentiment == "positive":
            impact_text = "偏利好"
        elif sentiment == "negative":
            impact_text = "偏利空"
        else:
            impact_text = "偏中性"
        keyword_text = "、".join((positive_hits + negative_hits)[:5]) or "暂无明显情绪关键词"
        return f"规则判断该新闻主要关联 {sector_text}，影响 {impact_text}，关键词：{keyword_text}。"

    def _risk_warnings(self, text: str) -> list[str]:
        warnings = []
        if "传闻" in text or "网传" in text:
            warnings.append("新闻包含传闻/网传表述，需要等待权威来源确认。")
        if "预计" in text or "有望" in text:
            warnings.append("新闻包含预期性表述，实际落地存在不确定性。")
        if "减持" in text or "处罚" in text or "调查" in text:
            warnings.append("新闻包含潜在风险事件，应优先做风控复核。")
        return warnings

    def _clean_text(self, text: str) -> str:
        return re.sub(r"\s+", " ", str(text or "")).strip()

    def _json_object(self, value: str | None) -> dict[str, Any]:
        if not value:
            return {}
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    def _json_list(self, value: str | None) -> list[Any]:
        if not value:
            return []
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else [parsed]
        except Exception:
            return [value]

    def _safe_float(self, value: Any, fallback: Any) -> float:
        try:
            return round(float(value), 3)
        except Exception:
            try:
                return round(float(fallback), 3)
            except Exception:
                return 0.5
