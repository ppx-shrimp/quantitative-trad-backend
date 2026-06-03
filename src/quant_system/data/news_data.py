from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


AVAILABLE_NEWS_SOURCES = [
    {
        "source": "eastmoney-akshare",
        "news_type": "news",
        "provider": "akshare.stock_news_em",
        "description": "东方财富 A 股资讯，适合热点新闻和市场快讯。",
    },
    {
        "source": "akshare-notice",
        "news_type": "notice",
        "provider": "akshare.stock_notice_report",
        "description": "A 股公告数据，适合个股公告和公司事件。",
    },
]


def _get_akshare():
    """按需导入 akshare，避免服务启动阶段被重型三方库导入卡住。"""
    import akshare as ak

    return ak


class NewsProvider:
    """新闻和事件数据适配层。

    第一版真实资讯以 AkShare 为主，增强东方财富新闻和公告两个可用源。所有外部
    接口失败都降级为空列表，由服务层继续使用缓存或本地已落库数据。
    """

    def __init__(self) -> None:
        self._source_status: dict[str, dict] = {
            item["source"]: {
                **item,
                "status": "unknown",
                "last_success_at": None,
                "last_error_at": None,
                "last_error": None,
                "last_count": 0,
            }
            for item in AVAILABLE_NEWS_SOURCES
        }

    def fetch_news(self, limit: int = 50) -> list[dict]:
        return self._fetch_with_status(
            source="eastmoney-akshare",
            news_type="news",
            provider="akshare.stock_news_em",
            fetcher=lambda: self._fetch_stock_news_em(limit=limit),
        )

    def fetch_notices(self, limit: int = 50) -> list[dict]:
        return self._fetch_with_status(
            source="akshare-notice",
            news_type="notice",
            provider="akshare.stock_notice_report",
            fetcher=lambda: self._fetch_stock_notice_report(limit=limit),
        )

    def source_status(self) -> dict:
        return {
            "items": list(self._source_status.values()),
            "count": len(self._source_status),
            "summary": "资讯源第一阶段使用 AkShare 增强：东方财富新闻 + A 股公告；失败时回退缓存/本地库。",
        }

    def mark_source_error(self, news_type: str, error: str) -> None:
        source = "akshare-notice" if news_type == "notice" else "eastmoney-akshare"
        current = self._source_status.get(source)
        if not current:
            return
        self._source_status[source] = {
            **current,
            "status": "error",
            "last_error_at": datetime.now(timezone.utc).isoformat(),
            "last_error": error,
            "last_count": 0,
        }

    def get_hot_news(self) -> list[dict]:
        return [
            {
                "title": "AI 算力板块活跃，资金关注高景气方向",
                "summary": "模拟热点新闻；真实数据源不可用时用于页面降级展示。",
                "source": "mock-news",
                "news_type": "news",
                "sentiment": "positive",
                "published_at": datetime.now(timezone.utc).isoformat(),
                "related_symbols": ["000001", "600000"],
                "related_sectors": ["AI算力"],
                "tags": ["AI", "算力"],
                "url": None,
                "raw": {},
            },
            {
                "title": "新能源产业链出现政策催化，短线热度提升",
                "summary": "模拟热点新闻；真实数据源不可用时用于页面降级展示。",
                "source": "mock-news",
                "news_type": "news",
                "sentiment": "neutral_positive",
                "published_at": datetime.now(timezone.utc).isoformat(),
                "related_symbols": ["300750"],
                "related_sectors": ["新能源"],
                "tags": ["新能源", "政策"],
                "url": None,
                "raw": {},
            },
        ]

    def _fetch_with_status(self, source: str, news_type: str, provider: str, fetcher) -> list[dict]:
        now = datetime.now(timezone.utc).isoformat()
        try:
            rows = fetcher()
            if not rows:
                raise RuntimeError("资讯源返回空数据，请检查 AkShare 接口可用性或稍后重试")
            self._source_status[source] = {
                "source": source,
                "news_type": news_type,
                "provider": provider,
                "description": self._source_status.get(source, {}).get("description", ""),
                "status": "ok",
                "last_success_at": now,
                "last_error_at": self._source_status.get(source, {}).get("last_error_at"),
                "last_error": None,
                "last_count": len(rows),
            }
            return rows
        except Exception as exc:
            self._source_status[source] = {
                "source": source,
                "news_type": news_type,
                "provider": provider,
                "description": self._source_status.get(source, {}).get("description", ""),
                "status": "error",
                "last_success_at": self._source_status.get(source, {}).get("last_success_at"),
                "last_error_at": now,
                "last_error": str(exc),
                "last_count": 0,
            }
            return []

    def _fetch_stock_news_em(self, limit: int) -> list[dict]:
        ak = _get_akshare()
        df = ak.stock_news_em()
        return [self._normalize_news_row(row, news_type="news", source="eastmoney-akshare") for row in self._df_rows(df, limit)]

    def _fetch_stock_notice_report(self, limit: int) -> list[dict]:
        ak = _get_akshare()
        try:
            df = ak.stock_notice_report(symbol="全部")
        except TypeError:
            df = ak.stock_notice_report()
        return [self._normalize_notice_row(row, source="akshare-notice") for row in self._df_rows(df, limit)]

    def _df_rows(self, df: Any, limit: int) -> list[dict]:
        if df is None:
            return []
        try:
            rows = df.head(limit).to_dict("records")
        except Exception:
            return []
        return [dict(row) for row in rows]

    def _normalize_news_row(self, row: dict, news_type: str, source: str) -> dict:
        title = self._first_text(row, ["新闻标题", "标题", "title", "news_title"])
        summary = self._first_text(row, ["新闻内容", "摘要", "summary", "content"])
        published_at = self._first_text(row, ["发布时间", "时间", "datetime", "publish_time", "date"])
        url = self._first_text(row, ["新闻链接", "链接", "url", "URL"])
        related_symbols = self._extract_symbols(row)
        return {
            "title": title,
            "summary": summary,
            "content": summary,
            "url": url,
            "source": source,
            "news_type": news_type,
            "published_at": self._normalize_time(published_at),
            "related_symbols": related_symbols,
            "related_sectors": [],
            "tags": self._infer_tags(title + " " + summary),
            "sentiment": None,
            "importance": self._infer_importance(title + " " + summary, news_type),
            "raw": row,
        }

    def _normalize_notice_row(self, row: dict, source: str) -> dict:
        title = self._first_text(row, ["公告标题", "标题", "title"])
        published_at = self._first_text(row, ["公告日期", "发布时间", "时间", "date"])
        url = self._first_text(row, ["公告链接", "链接", "url", "URL"])
        symbol = self._first_text(row, ["代码", "股票代码", "symbol"])
        name = self._first_text(row, ["名称", "股票简称", "name"])
        return {
            "title": title,
            "summary": name,
            "content": None,
            "url": url,
            "source": source,
            "news_type": "notice",
            "published_at": self._normalize_time(published_at),
            "related_symbols": [self._normalize_symbol(symbol)] if symbol else [],
            "related_sectors": [],
            "tags": self._infer_tags(title),
            "sentiment": None,
            "importance": self._infer_importance(title, "notice"),
            "raw": row,
        }

    def _first_text(self, row: dict, keys: list[str]) -> str:
        for key in keys:
            value = row.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text and text.lower() != "nan":
                return text
        return ""

    def _extract_symbols(self, row: dict) -> list[str]:
        raw = self._first_text(row, ["股票代码", "代码", "相关股票", "symbol", "symbols"])
        if not raw:
            return []
        parts = raw.replace("，", ",").replace(";", ",").split(",")
        symbols = [self._normalize_symbol(part) for part in parts if self._normalize_symbol(part)]
        return list(dict.fromkeys(symbols))

    def _normalize_symbol(self, value: str) -> str:
        text = str(value or "").strip().upper()
        if not text:
            return ""
        if "." in text:
            text = text.split(".")[0]
        return text.zfill(6) if text.isdigit() and len(text) < 6 else text

    def _normalize_time(self, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return datetime.now(timezone.utc).isoformat()
        return text

    def _infer_tags(self, text: str) -> list[str]:
        candidates = ["AI", "算力", "新能源", "机器人", "低空经济", "半导体", "并购", "减持", "增持", "业绩", "监管", "订单", "分红", "回购", "重组", "风险"]
        return [tag for tag in candidates if tag.lower() in text.lower()]

    def _infer_importance(self, text: str, news_type: str) -> float | None:
        keywords = ["重大", "停牌", "复牌", "重组", "并购", "回购", "减持", "增持", "业绩", "预增", "预亏", "监管", "风险", "处罚"]
        score = 0.5 + sum(0.08 for keyword in keywords if keyword in text)
        if news_type == "notice":
            score += 0.1
        return round(min(score, 1.0), 2)
