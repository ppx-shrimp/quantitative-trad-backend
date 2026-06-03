from __future__ import annotations

import concurrent.futures
import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from quant_system.api.pagination import PageParams, PageResult, paginate
from quant_system.data.news_data import NewsProvider
from quant_system.db.database import SessionLocal, engine, init_sqlalchemy_tables
from quant_system.db.models import MarketNewsModel
from quant_system.services.news_cache_service import NewsCacheService


class NewsService:
    def __init__(self) -> None:
        self.news_provider = NewsProvider()
        self.cache = NewsCacheService()
        init_sqlalchemy_tables()

    def get_hot_news(self) -> dict:
        items = self.list_news_page(PageParams(page=1, page_size=20), news_type="news").items
        source = "local_db"
        if not items:
            cached = self.cache.get_json(self.cache.build_source_key("hot_mock", 20))
            if isinstance(cached, list):
                items = cached
                source = "cache_mock"
            else:
                items = self.news_provider.get_hot_news()
                self.cache.set_json(self.cache.build_source_key("hot_mock", 20), items)
                source = "mock"
        return {
            "items": items,
            "summary": "真实资讯优先读取本地 market_news；无本地数据时降级模拟热点。",
            "source": source,
            "cache": self.cache.status(),
        }

    def sync_news(self, news_types: list[str] | None = None, limit: int = 50, force_refresh: bool = False) -> dict:
        normalized_types = news_types or ["news", "notice"]
        fetched_rows: list[dict] = []
        provider_errors: list[str] = []
        cache_hits: list[str] = []
        saved_count = 0
        for news_type in normalized_types:
            try:
                rows = self._fetch_source_rows(news_type=news_type, limit=limit, force_refresh=force_refresh)
                if rows and rows[0].get("_cache_hit"):
                    cache_hits.append(news_type)
                    rows = [{k: v for k, v in row.items() if k != "_cache_hit"} for row in rows]
                fetched_rows.extend(rows)
            except Exception as exc:
                error_message = str(exc)
                self.news_provider.mark_source_error(news_type, error_message)
                provider_errors.append(f"{news_type}: {error_message}")
        if fetched_rows:
            try:
                saved_count = self.save_news(fetched_rows)
            except Exception as exc:
                provider_errors.append(f"db_save: {exc}")
        if saved_count:
            self.cache.invalidate_queries()
        if provider_errors and not saved_count:
            status = "failed"
        elif provider_errors:
            status = "partial_success"
        else:
            status = "success"
        return {
            "status": status,
            "news_types": normalized_types,
            "fetched_count": len(fetched_rows),
            "saved_count": saved_count,
            "cache_hits": cache_hits,
            "provider_errors": provider_errors,
            "source_status": self.news_provider.source_status(),
            "cache": self.cache.status(),
            "message": "资讯同步完成；查询接口始终优先读取本地 market_news。",
        }

    def save_news(self, rows: list[dict]) -> int:
        now = self._now()
        values_list = []
        for row in rows:
            normalized = self._normalize_row(row, fetched_at=now)
            if not normalized["title"]:
                continue
            values_list.append(normalized)
        if not values_list:
            return 0
        with SessionLocal() as session:
            self._upsert_news(session, values_list)
            session.commit()
        return len(values_list)

    def list_news_page(
        self,
        page_params: PageParams,
        news_type: str | None = None,
        source: str | None = None,
        symbol: str | None = None,
        keyword: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        use_cache: bool = True,
    ) -> PageResult:
        cache_key = self.cache.build_query_key(
            {
                "page": page_params.page,
                "page_size": page_params.page_size,
                "news_type": news_type,
                "source": source,
                "symbol": self._normalize_symbol(symbol) if symbol else None,
                "keyword": keyword,
                "start_date": start_date,
                "end_date": end_date,
            }
        )
        if use_cache:
            cached = self.cache.get_json(cache_key)
            if isinstance(cached, dict):
                return PageResult(
                    items=cached.get("items") or [],
                    total=int(cached.get("total") or 0),
                    page=int(cached.get("page") or page_params.page),
                    page_size=int(cached.get("page_size") or page_params.page_size),
                    total_pages=int(cached.get("total_pages") or 1),
                )

        stmt = select(MarketNewsModel).order_by(MarketNewsModel.published_at.desc(), MarketNewsModel.id.desc())
        if news_type:
            stmt = stmt.where(MarketNewsModel.news_type == news_type)
        if source:
            stmt = stmt.where(MarketNewsModel.source == source)
        if symbol:
            normalized_symbol = self._normalize_symbol(symbol)
            stmt = stmt.where(MarketNewsModel.related_symbols.like(f"%{normalized_symbol}%"))
        if keyword:
            like_value = f"%{keyword}%"
            stmt = stmt.where(or_(MarketNewsModel.title.like(like_value), MarketNewsModel.summary.like(like_value), MarketNewsModel.tags.like(like_value)))
        if start_date:
            stmt = stmt.where(MarketNewsModel.published_at >= start_date)
        if end_date:
            stmt = stmt.where(MarketNewsModel.published_at <= end_date + "T23:59:59")
        with SessionLocal() as session:
            result = paginate(session, stmt, None, page_params, to_dict_fn=self._news_to_dict)
        if use_cache:
            self.cache.set_json(cache_key, result.to_dict())
        return result

    def list_symbol_news(self, symbol: str, page_params: PageParams, news_type: str | None = None) -> PageResult:
        return self.list_news_page(page_params=page_params, symbol=symbol, news_type=news_type)

    def source_status(self) -> dict:
        latest_by_type: dict[str, str | None] = {"news": None, "notice": None}
        total_by_type: dict[str, int] = {"news": 0, "notice": 0}
        db_error: str | None = None
        rows = []
        try:
            with SessionLocal() as session:
                rows = session.execute(
                    select(MarketNewsModel.news_type, func.count(MarketNewsModel.id), func.max(MarketNewsModel.published_at)).group_by(MarketNewsModel.news_type)
                ).all()
        except Exception as exc:
            db_error = str(exc)
        for news_type, total, latest in rows:
            total_by_type[str(news_type)] = int(total or 0)
            latest_by_type[str(news_type)] = latest
        return {
            "provider": self.news_provider.source_status(),
            "local_db": {
                "total_by_type": total_by_type,
                "latest_by_type": latest_by_type,
                "status": "error" if db_error else "ok",
                "error": db_error,
            },
            "cache": self.cache.status(),
        }

    def invalidate_cache(self) -> dict:
        deleted = self.cache.invalidate_all()
        return {
            "deleted": deleted,
            "cache": self.cache.status(),
            "message": "资讯缓存已清理；Redis 未启用时 deleted 为 0。",
        }

    def _fetch_source_rows(self, news_type: str, limit: int, force_refresh: bool) -> list[dict]:
        cache_key = self.cache.build_source_key(news_type, limit)
        if not force_refresh:
            cached = self.cache.get_json(cache_key)
            if isinstance(cached, list):
                return [{**row, "_cache_hit": True} for row in cached]
        if news_type == "notice":
            rows = self._run_fetch_with_timeout(lambda: self.news_provider.fetch_notices(limit=limit), news_type=news_type)
        elif news_type == "news":
            rows = self._run_fetch_with_timeout(lambda: self.news_provider.fetch_news(limit=limit), news_type=news_type)
        else:
            rows = []
        self.cache.set_json(cache_key, rows)
        return rows

    def _run_fetch_with_timeout(self, fetcher, news_type: str) -> list[dict]:
        timeout_seconds = getattr(self, "_run_fetch_timeout_seconds", 12)
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = executor.submit(fetcher)
        try:
            return future.result(timeout=timeout_seconds)
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
            raise TimeoutError(f"{news_type} 资讯源拉取超过 {timeout_seconds} 秒，已中断本次同步") from exc
        except Exception:
            executor.shutdown(wait=False, cancel_futures=True)
            raise
        finally:
            if future.done():
                executor.shutdown(wait=False, cancel_futures=True)

    def _normalize_row(self, row: dict, fetched_at: str) -> dict:
        title = str(row.get("title") or "").strip()
        published_at = str(row.get("published_at") or fetched_at).strip()
        source = str(row.get("source") or "akshare").strip()
        news_type = str(row.get("news_type") or "news").strip()
        fingerprint = self._fingerprint(source, news_type, title, published_at, row.get("url"))
        related_symbols = self._json_list(row.get("related_symbols"))
        related_sectors = self._json_list(row.get("related_sectors"))
        tags = self._json_list(row.get("tags"))
        raw_json = json.dumps(row.get("raw") or row, ensure_ascii=False, default=str)
        return {
            "news_id": fingerprint[:32],
            "fingerprint": fingerprint,
            "title": title,
            "summary": row.get("summary") or None,
            "content": row.get("content") or None,
            "url": row.get("url") or None,
            "source": source,
            "news_type": news_type,
            "published_at": published_at,
            "fetched_at": fetched_at,
            "related_symbols": related_symbols,
            "related_sectors": related_sectors,
            "tags": tags,
            "sentiment": row.get("sentiment") or None,
            "importance": self._to_float(row.get("importance")),
            "raw_json": raw_json,
            "created_at": fetched_at,
            "updated_at": fetched_at,
            "created_by": "system",
            "updated_by": "system",
        }

    def _upsert_news(self, session, values_list: list[dict]) -> None:
        dialect = engine.dialect.name
        if dialect == "mysql":
            stmt = mysql_insert(MarketNewsModel).values(values_list)
            update_values = {
                "title": stmt.inserted.title,
                "summary": stmt.inserted.summary,
                "content": stmt.inserted.content,
                "url": stmt.inserted.url,
                "source": stmt.inserted.source,
                "news_type": stmt.inserted.news_type,
                "published_at": stmt.inserted.published_at,
                "fetched_at": stmt.inserted.fetched_at,
                "related_symbols": stmt.inserted.related_symbols,
                "related_sectors": stmt.inserted.related_sectors,
                "tags": stmt.inserted.tags,
                "sentiment": stmt.inserted.sentiment,
                "importance": stmt.inserted.importance,
                "raw_json": stmt.inserted.raw_json,
                "updated_at": stmt.inserted.updated_at,
                "updated_by": stmt.inserted.updated_by,
            }
            session.execute(stmt.on_duplicate_key_update(**update_values))
            return
        if dialect == "sqlite":
            stmt = sqlite_insert(MarketNewsModel).values(values_list)
            update_values = {
                "title": getattr(stmt.excluded, "title"),
                "summary": stmt.excluded.summary,
                "content": stmt.excluded.content,
                "url": stmt.excluded.url,
                "source": stmt.excluded.source,
                "news_type": stmt.excluded.news_type,
                "published_at": stmt.excluded.published_at,
                "fetched_at": stmt.excluded.fetched_at,
                "related_symbols": stmt.excluded.related_symbols,
                "related_sectors": stmt.excluded.related_sectors,
                "tags": stmt.excluded.tags,
                "sentiment": stmt.excluded.sentiment,
                "importance": stmt.excluded.importance,
                "raw_json": stmt.excluded.raw_json,
                "updated_at": stmt.excluded.updated_at,
                "updated_by": stmt.excluded.updated_by,
            }
            session.execute(stmt.on_conflict_do_update(index_elements=["fingerprint"], set_=update_values))
            return
        for values in values_list:
            existing = session.scalar(select(MarketNewsModel).where(MarketNewsModel.fingerprint == values["fingerprint"]))
            if existing is None:
                session.add(MarketNewsModel(**values))
            else:
                for key, value in values.items():
                    if key != "created_at":
                        setattr(existing, key, value)

    def _news_to_dict(self, row: MarketNewsModel) -> dict:
        return {
            "id": row.id,
            "news_id": row.news_id,
            "fingerprint": row.fingerprint,
            "title": row.title,
            "summary": row.summary,
            "content": row.content,
            "url": row.url,
            "source": row.source,
            "news_type": row.news_type,
            "published_at": row.published_at,
            "fetched_at": row.fetched_at,
            "related_symbols": self._loads_list(row.related_symbols),
            "related_sectors": self._loads_list(row.related_sectors),
            "tags": self._loads_list(row.tags),
            "sentiment": row.sentiment,
            "importance": row.importance,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    def _fingerprint(self, *parts: Any) -> str:
        raw = "|".join(str(part or "") for part in parts)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _json_list(self, value: Any) -> str:
        if value is None:
            items: list[str] = []
        elif isinstance(value, list):
            items = [str(item).strip() for item in value if str(item).strip()]
        else:
            items = [str(value).strip()] if str(value).strip() else []
        return json.dumps(list(dict.fromkeys(items)), ensure_ascii=False)

    def _loads_list(self, value: str | None) -> list[str]:
        if not value:
            return []
        try:
            loaded = json.loads(value)
            return loaded if isinstance(loaded, list) else []
        except Exception:
            return []

    def _normalize_symbol(self, symbol: str | None) -> str:
        text = str(symbol or "").strip().upper()
        if "." in text:
            text = text.split(".")[0]
        return text.zfill(6) if text.isdigit() and len(text) < 6 else text

    def _to_float(self, value: Any) -> float | None:
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()
