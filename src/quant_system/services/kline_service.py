from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from quant_system.api.pagination import PageParams, PageResult, paginate
from quant_system.core.config import settings
from quant_system.data.market_data import MarketDataProvider
from quant_system.db.database import SessionLocal, engine, init_sqlalchemy_tables
from quant_system.db.models import KlineSyncLogModel, StockKlineModel
from quant_system.services.kline_cache_service import KlineCacheService
from quant_system.services.stock_pool_service import StockPoolService


class KlineService:
    def __init__(self) -> None:
        self.market_data = MarketDataProvider()
        self.stock_pool_service = StockPoolService()
        self.kline_cache = KlineCacheService()
        self.initialize()

    def initialize(self) -> None:
        init_sqlalchemy_tables()

    def sync_pool_klines(
        self,
        pool_code: str,
        periods: list[str] | None = None,
        limit_symbols: int | None = None,
        tracked: bool = True,
    ) -> dict:
        if tracked:
            from quant_system.services.task_execution_service import TaskExecutionService

            return TaskExecutionService().run_tracked(
                task_name="sync_pool_klines",
                task_type="data_sync",
                trigger_type="manual_api",
                params={"pool_code": pool_code, "periods": periods, "limit_symbols": limit_symbols},
                fn=lambda: self.sync_pool_klines(
                    pool_code=pool_code,
                    periods=periods,
                    limit_symbols=limit_symbols,
                    tracked=False,
                ),
            )
        periods = periods or ["daily", "minute"]
        coverage_before_sync = self.inspect_pool_klines(
            pool_code=pool_code,
            periods=periods,
            limit_symbols=limit_symbols,
        )
        stock_members = coverage_before_sync["items"]
        results = []
        batch_size = max(1, settings.kline_sync_batch_size)
        for member_index, member in enumerate(stock_members):
            symbol = member["symbol"]
            for period in periods:
                period_info = member["periods"][period]
                if not period_info["needs_sync"]:
                    results.append(self._build_local_ready_result(symbol, period, period_info["rows_count"]))
                    continue
                try:
                    item = self.sync_symbol_kline(symbol=symbol, period=period, pool_code=pool_code, tracked=False)
                except Exception as exc:
                    item = {
                        "symbol": self._normalize_symbol(symbol),
                        "period": period,
                        "status": "failed",
                        "rows_count": 0,
                        "source": "batch_exception",
                        "cache_enabled": self.kline_cache.enabled,
                        "fallback_used": False,
                        "attempts": 0,
                        "provider_errors": [str(exc)],
                        "message": str(exc),
                    }
                results.append(item)
                if period == "daily":
                    time.sleep(settings.kline_sync_symbol_pause_seconds)
            if member_index > 0 and (member_index + 1) % batch_size == 0:
                time.sleep(settings.kline_sync_batch_pause_seconds)
        return self._build_pool_sync_result(
            pool_code=pool_code,
            periods=periods,
            symbol_count=len(stock_members),
            results=results,
            coverage_before_sync=coverage_before_sync,
        )

    def inspect_pool_klines(
        self,
        pool_code: str,
        periods: list[str] | None = None,
        limit_symbols: int | None = None,
        min_rows: int | None = None,
    ) -> dict:
        periods = periods or ["daily", "minute"]
        min_rows = int(min_rows or settings.kline_sync_min_rows)
        members = self.stock_pool_service.list_members(pool_code)
        stock_members = [member for member in members if self._is_stock_symbol(member["symbol"])]
        if limit_symbols is not None:
            stock_members = stock_members[:limit_symbols]

        symbols = [self._normalize_symbol(member["symbol"]) for member in stock_members]
        stats_map = self._get_pool_local_kline_stats(symbols, periods)
        items = []
        ready_symbols = []
        needs_sync_symbols = []
        missing_symbols_by_period = {period: [] for period in periods}
        insufficient_symbols_by_period = {period: [] for period in periods}

        for member in stock_members:
            symbol = self._normalize_symbol(member["symbol"])
            period_items: dict[str, dict] = {}
            pending_periods: list[str] = []
            for period in periods:
                stats = stats_map.get((symbol, period), self._empty_kline_stats())
                rows_count = int(stats["rows_count"])
                if rows_count <= 0:
                    state = "missing"
                    needs_sync = True
                    missing_symbols_by_period[period].append(symbol)
                elif rows_count < min_rows:
                    state = "insufficient"
                    needs_sync = True
                    insufficient_symbols_by_period[period].append(symbol)
                else:
                    state = "ready"
                    needs_sync = False
                if needs_sync:
                    pending_periods.append(period)
                period_items[period] = {
                    **stats,
                    "state": state,
                    "needs_sync": needs_sync,
                    "meets_min_rows": rows_count >= min_rows,
                }
            item = {
                "symbol": symbol,
                "name": member.get("name"),
                "needs_sync": bool(pending_periods),
                "pending_periods": pending_periods,
                "periods": period_items,
            }
            items.append(item)
            if item["needs_sync"]:
                needs_sync_symbols.append(symbol)
            else:
                ready_symbols.append(symbol)

        return {
            "pool_code": pool_code,
            "periods": periods,
            "min_rows": min_rows,
            "symbol_count": len(stock_members),
            "ready_symbol_count": len(ready_symbols),
            "needs_sync_symbol_count": len(needs_sync_symbols),
            "pending_target_count": sum(len(item["pending_periods"]) for item in items),
            "summary": {
                "ready_symbols": ready_symbols,
                "needs_sync_symbols": needs_sync_symbols,
                "missing_symbols_by_period": missing_symbols_by_period,
                "insufficient_symbols_by_period": insufficient_symbols_by_period,
            },
            "items": items,
        }

    def _build_pool_sync_result(
        self,
        pool_code: str,
        periods: list[str],
        symbol_count: int,
        results: list[dict],
        coverage_before_sync: dict | None = None,
    ) -> dict:
        success_items = [item for item in results if item.get("status") == "success"]
        failed_items = [item for item in results if item.get("status") == "failed"]
        fallback_items = [item for item in success_items if item.get("fallback_used")]
        skipped_items = [item for item in success_items if int(item.get("attempts") or 0) == 0]
        external_success_items = [
            item
            for item in success_items
            if not item.get("fallback_used") and int(item.get("attempts") or 0) > 0
        ]
        failure_reasons: dict[str, int] = {}
        for item in failed_items:
            message = str(item.get("message") or "unknown")
            failure_reasons[message] = failure_reasons.get(message, 0) + 1
        pending_targets = []
        if coverage_before_sync:
            pending_targets = [
                {
                    "symbol": item["symbol"],
                    "pending_periods": item["pending_periods"],
                }
                for item in coverage_before_sync.get("items", [])
                if item.get("needs_sync")
            ]
        return {
            "pool_code": pool_code,
            "symbol_count": symbol_count,
            "periods": periods,
            "total_tasks": len(results),
            "status": "success" if not failed_items else ("partial_success" if success_items else "failed"),
            "success_count": len(success_items),
            "failed_count": len(failed_items),
            "fallback_count": len(fallback_items),
            "skipped_count": len(skipped_items),
            "external_success_count": len(external_success_items),
            "cache_enabled": self.kline_cache.enabled,
            "coverage_before_sync": coverage_before_sync,
            "pending_targets": pending_targets,
            "summary": {
                "success_symbols": sorted({item.get("symbol") for item in success_items if item.get("symbol")}),
                "fallback_symbols": sorted({item.get("symbol") for item in fallback_items if item.get("symbol")}),
                "failed_symbols": sorted({item.get("symbol") for item in failed_items if item.get("symbol")}),
                "failed_targets": [
                    {"symbol": item.get("symbol"), "period": item.get("period"), "message": item.get("message")}
                    for item in failed_items
                ],
                "failure_reasons": failure_reasons,
            },
            "groups": {
                "external_success": external_success_items,
                "local_fallback": fallback_items,
                "skipped_local_ready": skipped_items,
                "failed": failed_items,
            },
            "results": results,
        }

    def sync_symbol_kline(self, symbol: str, period: str = "daily", pool_code: str | None = None, tracked: bool = True) -> dict:
        if tracked:
            from quant_system.services.task_execution_service import TaskExecutionService

            return TaskExecutionService().run_tracked(
                task_name="sync_symbol_kline",
                task_type="data_sync",
                trigger_type="manual_api",
                params={"symbol": symbol, "period": period, "pool_code": pool_code},
                fn=lambda: self.sync_symbol_kline(
                    symbol=symbol,
                    period=period,
                    pool_code=pool_code,
                    tracked=False,
                ),
            )
        normalized_symbol = self._normalize_symbol(symbol)
        try:
            existing_rows = self.list_klines(normalized_symbol, period=period, limit=settings.kline_sync_min_rows)
            if len(existing_rows) >= settings.kline_sync_min_rows:
                result = {
                    "symbol": normalized_symbol,
                    "period": period,
                    "status": "success",
                    "rows_count": len(existing_rows),
                    "source": "local_cache_or_db",
                    "cache_enabled": self.kline_cache.enabled,
                    "fallback_used": False,
                    "attempts": 0,
                    "message": "本地已有足够 K 线，跳过外部同步",
                }
                self._log_sync(pool_code=pool_code, **result)
                return result
            fetch_result = self._fetch_kline_with_retry(normalized_symbol, period)
            saved_count = self.save_klines(normalized_symbol, period, fetch_result["rows"])
            result = {
                "symbol": normalized_symbol,
                "period": period,
                "status": "success",
                "rows_count": saved_count,
                "source": fetch_result["source"],
                "cache_enabled": self.kline_cache.enabled,
                "fallback_used": False,
                "attempts": fetch_result["attempts"],
                "provider_errors": fetch_result["errors"],
                "message": "ok，已写入本地数据库并刷新 Redis K 线缓存" if self.kline_cache.enabled else "ok，已写入本地数据库",
            }
        except Exception as exc:
            fallback_rows = self._list_klines_from_db(normalized_symbol, period=period, limit=120)
            if fallback_rows:
                result = {
                    "symbol": normalized_symbol,
                    "period": period,
                    "status": "success",
                    "rows_count": len(fallback_rows),
                    "source": "local_db_fallback",
                    "cache_enabled": self.kline_cache.enabled,
                    "fallback_used": True,
                    "attempts": settings.kline_sync_retry_count,
                    "provider_errors": [str(exc)],
                    "message": f"外部 K 线接口不可用，已回退本地数据库缓存：{exc}",
                }
                self.kline_cache.set_klines(normalized_symbol, period, 120, fallback_rows)
            else:
                result = {
                    "symbol": normalized_symbol,
                    "period": period,
                    "status": "failed",
                    "rows_count": 0,
                    "source": "external_provider",
                    "cache_enabled": self.kline_cache.enabled,
                    "fallback_used": False,
                    "attempts": settings.kline_sync_retry_count,
                    "provider_errors": [str(exc)],
                    "message": str(exc),
                }
        self._log_sync(pool_code=pool_code, **result)
        return result

    def save_klines(self, symbol: str, period: str, rows: list[dict]) -> int:
        now = self._now()
        values_list = []
        for row in rows:
            trade_time = row.get("date") or row.get("datetime") or row.get("trade_time")
            if not trade_time:
                continue
            values_list.append({
                "symbol": symbol,
                "period": period,
                "trade_time": str(trade_time),
                "open": self._to_float(row.get("open")),
                "high": self._to_float(row.get("high")),
                "low": self._to_float(row.get("low")),
                "close": self._to_float(row.get("close")),
                "volume": self._to_float(row.get("volume")),
                "amount": self._to_float(row.get("amount")),
                "change_pct": self._to_float(row.get("change_pct")),
                "turnover_rate": self._to_float(row.get("turnover_rate")),
                "source": row.get("source") or "akshare",
                "created_at": now,
                "updated_at": now,
                "created_by": "system",
                "updated_by": "system",
            })
        if not values_list:
            return 0
        with SessionLocal() as session:
            self._upsert_klines(session, values_list)
            session.commit()
        self._refresh_kline_cache(symbol, period)
        return len(values_list)

    def list_klines(self, symbol: str, period: str = "daily", limit: int = 120) -> list[dict]:
        return self.list_klines_with_meta(symbol=symbol, period=period, limit=limit)["items"]

    def list_klines_with_meta(self, symbol: str, period: str = "daily", limit: int = 120) -> dict:
        normalized = self._normalize_symbol(symbol)
        normalized_period = self._normalize_period(period)
        if self._is_derived_period(normalized_period):
            rows = self._list_derived_klines_from_daily(
                normalized,
                period=normalized_period,
                limit=limit,
            )
            return {
                "symbol": normalized,
                "period": normalized_period,
                "items": rows,
                "count": len(rows),
                "total_count": self._count_derived_klines_from_daily(normalized, period=normalized_period),
                "source": "local_aggregated",
                "cache_hit": False,
                "cache_enabled": self.kline_cache.enabled,
            }
        total_count = self._count_klines_from_db(normalized, period=normalized_period)
        cached_rows = self.kline_cache.get_klines(normalized, normalized_period, limit)
        if cached_rows is not None:
            return {
                "symbol": normalized,
                "period": normalized_period,
                "items": cached_rows,
                "count": len(cached_rows),
                "total_count": total_count,
                "source": "redis",
                "cache_hit": True,
                "cache_enabled": self.kline_cache.enabled,
            }
        rows = self._list_klines_from_db(normalized, period=normalized_period, limit=limit)
        if rows:
            self.kline_cache.set_klines(normalized, normalized_period, limit, rows)
        return {
            "symbol": normalized,
            "period": normalized_period,
            "items": rows,
            "count": len(rows),
            "total_count": total_count,
            "source": "local_db",
            "cache_hit": False,
            "cache_enabled": self.kline_cache.enabled,
        }

    def get_display_klines(
        self,
        symbol: str,
        period: str = "daily",
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int | None = None,
    ) -> dict:
        normalized = self._normalize_symbol(symbol)
        normalized_period = self._normalize_period(period)
        local_limit = limit or (2000 if normalized_period in {"daily", "weekly", "monthly"} else 500)
        if start_date or end_date:
            rows = self._list_klines_for_display_from_local(
                normalized,
                period=normalized_period,
                limit=None,
                start_date=start_date,
                end_date=end_date,
            )
            if rows:
                return {
                    "symbol": normalized,
                    "period": normalized_period,
                    "items": rows,
                    "count": len(rows),
                    "source": "local_aggregated" if self._is_derived_period(normalized_period) else "local_db",
                    "cache_hit": False,
                    "cache_enabled": self.kline_cache.enabled,
                    "fallback_used": False,
                }
        else:
            meta = self.list_klines_with_meta(normalized, period=normalized_period, limit=local_limit)
            if meta["items"]:
                return {
                    "symbol": normalized,
                    "period": normalized_period,
                    "items": meta["items"],
                    "count": meta["count"],
                    "source": meta["source"],
                    "cache_hit": meta["cache_hit"],
                    "cache_enabled": meta["cache_enabled"],
                    "fallback_used": False,
                }

        self._ensure_local_base_klines(normalized, normalized_period)
        meta = self.list_klines_with_meta(normalized, period=normalized_period, limit=local_limit)
        if meta["items"]:
            return {
                "symbol": normalized,
                "period": normalized_period,
                "items": meta["items"],
                "count": meta["count"],
                "source": meta["source"],
                "cache_hit": meta["cache_hit"],
                "cache_enabled": meta["cache_enabled"],
                "fallback_used": False,
            }

        try:
            fetch_result = self._fetch_kline_with_retry(normalized, normalized_period)
            return {
                "symbol": normalized,
                "period": normalized_period,
                "items": fetch_result["rows"],
                "count": len(fetch_result["rows"]),
                "source": fetch_result["source"],
                "cache_hit": False,
                "cache_enabled": self.kline_cache.enabled,
                "fallback_used": True,
            }
        except Exception:
            synthetic_rows = self._build_synthetic_klines(normalized, normalized_period, limit=local_limit)
            if synthetic_rows:
                return {
                    "symbol": normalized,
                    "period": normalized_period,
                    "items": synthetic_rows,
                    "count": len(synthetic_rows),
                    "source": "local_snapshot_fallback",
                    "cache_hit": False,
                    "cache_enabled": self.kline_cache.enabled,
                    "fallback_used": True,
                }
            raise

    def _list_klines_for_display_from_local(
        self,
        symbol: str,
        period: str,
        limit: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict]:
        normalized_period = self._normalize_period(period)
        if self._is_derived_period(normalized_period):
            return self._list_derived_klines_from_daily(
                symbol,
                period=normalized_period,
                limit=limit,
                start_date=start_date,
                end_date=end_date,
            )
        return self._list_klines_from_db(
            symbol,
            period=normalized_period,
            limit=limit,
            start_date=start_date,
            end_date=end_date,
        )

    def _list_klines_from_db(
        self,
        symbol: str,
        period: str = "daily",
        limit: int | None = 120,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict]:
        stmt = (
            select(StockKlineModel)
            .where(
                StockKlineModel.symbol == self._normalize_symbol(symbol),
                StockKlineModel.period == period,
            )
            .order_by(StockKlineModel.trade_time.desc())
        )
        if start_date:
            stmt = stmt.where(StockKlineModel.trade_time >= start_date)
        if end_date:
            stmt = stmt.where(StockKlineModel.trade_time <= end_date)
        if limit is not None:
            stmt = stmt.limit(limit)
        with SessionLocal() as session:
            rows = session.scalars(stmt).all()
            return [self._kline_to_dict(row) for row in reversed(rows)]

    def _count_klines_from_db(
        self,
        symbol: str,
        period: str = "daily",
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> int:
        stmt = select(func.count(StockKlineModel.id)).where(
            StockKlineModel.symbol == self._normalize_symbol(symbol),
            StockKlineModel.period == period,
        )
        if start_date:
            stmt = stmt.where(StockKlineModel.trade_time >= start_date)
        if end_date:
            stmt = stmt.where(StockKlineModel.trade_time <= end_date)
        with SessionLocal() as session:
            return int(session.scalar(stmt) or 0)

    def list_klines_page(self, symbol: str, period: str, page_params: PageParams) -> PageResult:
        normalized = self._normalize_symbol(symbol)
        normalized_period = self._normalize_period(period)
        if self._is_derived_period(normalized_period):
            rows = self._list_derived_klines_from_daily(normalized, period=normalized_period, limit=None)
            total = len(rows)
            paged_items = rows[page_params.offset : page_params.offset + page_params.limit]
            total_pages = max(1, (total + page_params.page_size - 1) // page_params.page_size) if total > 0 else 1
            return PageResult(
                items=paged_items,
                total=total,
                page=page_params.page,
                page_size=page_params.page_size,
                total_pages=total_pages,
            )
        stmt = (
            select(StockKlineModel)
            .where(StockKlineModel.symbol == normalized, StockKlineModel.period == normalized_period)
            .order_by(StockKlineModel.trade_time.desc())
        )
        with SessionLocal() as session:
            result = paginate(session, stmt, None, page_params, to_dict_fn=self._kline_to_dict)
            result.items = list(reversed(result.items))
            return result

    def get_kline_summary(self) -> list[dict]:
        with SessionLocal() as session:
            rows = session.execute(
                select(
                    StockKlineModel.symbol,
                    StockKlineModel.period,
                    func.count(StockKlineModel.id).label("rows_count"),
                    func.min(StockKlineModel.trade_time).label("first_time"),
                    func.max(StockKlineModel.trade_time).label("last_time"),
                )
                .group_by(StockKlineModel.symbol, StockKlineModel.period)
                .order_by(StockKlineModel.symbol, StockKlineModel.period)
            ).all()
            return [
                {
                    "symbol": symbol,
                    "period": period,
                    "rows_count": int(rows_count),
                    "first_time": first_time,
                    "last_time": last_time,
                }
                for symbol, period, rows_count, first_time, last_time in rows
            ]

    def get_kline_summary_page(self, page_params: PageParams) -> PageResult:
        subq = (
            select(
                StockKlineModel.symbol,
                StockKlineModel.period,
                func.count(StockKlineModel.id).label("rows_count"),
                func.min(StockKlineModel.trade_time).label("first_time"),
                func.max(StockKlineModel.trade_time).label("last_time"),
            )
            .group_by(StockKlineModel.symbol, StockKlineModel.period)
            .subquery()
        )

        data_stmt = select(subq).order_by(subq.c.symbol, subq.c.period)
        count_stmt = select(func.count()).select_from(subq)

        def _to_dict(row):
            return {
                "symbol": row.symbol,
                "period": row.period,
                "rows_count": int(row.rows_count),
                "first_time": row.first_time,
                "last_time": row.last_time,
            }

        with SessionLocal() as session:
            return paginate(session, data_stmt, count_stmt, page_params, to_dict_fn=_to_dict, use_scalars=False)

    def _upsert_klines(self, session, values_list: list[dict]) -> None:
        dialect = engine.dialect.name
        if dialect == "mysql":
            stmt = mysql_insert(StockKlineModel).values(values_list)
            update_values = {
                "open": getattr(stmt.inserted, "open"),
                "high": stmt.inserted.high,
                "low": stmt.inserted.low,
                "close": getattr(stmt.inserted, "close"),
                "volume": stmt.inserted.volume,
                "amount": stmt.inserted.amount,
                "change_pct": stmt.inserted.change_pct,
                "turnover_rate": stmt.inserted.turnover_rate,
                "source": stmt.inserted.source,
                "updated_at": stmt.inserted.updated_at,
                "updated_by": stmt.inserted.updated_by,
            }
            session.execute(stmt.on_duplicate_key_update(**update_values))
            return
        if dialect == "sqlite":
            stmt = sqlite_insert(StockKlineModel).values(values_list)
            update_values = {
                "open": getattr(stmt.excluded, "open"),
                "high": stmt.excluded.high,
                "low": stmt.excluded.low,
                "close": getattr(stmt.excluded, "close"),
                "volume": stmt.excluded.volume,
                "amount": stmt.excluded.amount,
                "change_pct": stmt.excluded.change_pct,
                "turnover_rate": stmt.excluded.turnover_rate,
                "source": stmt.excluded.source,
                "updated_at": stmt.excluded.updated_at,
                "updated_by": stmt.excluded.updated_by,
            }
            session.execute(stmt.on_conflict_do_update(index_elements=["symbol", "period", "trade_time"], set_=update_values))
            return
        for values in values_list:
            existing = session.scalar(
                select(StockKlineModel).where(
                    StockKlineModel.symbol == values["symbol"],
                    StockKlineModel.period == values["period"],
                    StockKlineModel.trade_time == values["trade_time"],
                )
            )
            if existing is None:
                session.add(StockKlineModel(**values))
            else:
                for key, value in values.items():
                    if key != "created_at":
                        setattr(existing, key, value)

    def _refresh_kline_cache(self, symbol: str, period: str) -> None:
        normalized = self._normalize_symbol(symbol)
        self.kline_cache.invalidate_klines(normalized, period)
        rows = self._list_klines_from_db(normalized, period=period, limit=120)
        if rows:
            self.kline_cache.set_klines(normalized, period, 120, rows)

    def _get_pool_local_kline_stats(self, symbols: list[str], periods: list[str]) -> dict[tuple[str, str], dict]:
        if not symbols or not periods:
            return {}
        with SessionLocal() as session:
            rows = session.execute(
                select(
                    StockKlineModel.symbol,
                    StockKlineModel.period,
                    func.count(StockKlineModel.id).label("rows_count"),
                    func.min(StockKlineModel.trade_time).label("first_time"),
                    func.max(StockKlineModel.trade_time).label("last_time"),
                    func.max(StockKlineModel.updated_at).label("last_updated_at"),
                )
                .where(
                    StockKlineModel.symbol.in_(symbols),
                    StockKlineModel.period.in_(periods),
                )
                .group_by(StockKlineModel.symbol, StockKlineModel.period)
            ).all()
        return {
            (str(row.symbol), str(row.period)): {
                "rows_count": int(row.rows_count or 0),
                "first_time": row.first_time,
                "last_time": row.last_time,
                "last_updated_at": row.last_updated_at,
            }
            for row in rows
        }

    def _empty_kline_stats(self) -> dict:
        return {
            "rows_count": 0,
            "first_time": None,
            "last_time": None,
            "last_updated_at": None,
        }

    def _list_derived_klines_from_daily(
        self,
        symbol: str,
        period: str,
        limit: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict]:
        daily_rows = self._list_klines_from_db(
            symbol,
            period="daily",
            limit=None,
            start_date=start_date,
            end_date=end_date,
        )
        aggregated = self._aggregate_from_daily(daily_rows, period)
        if limit is None:
            return aggregated
        return aggregated[-limit:]

    def _count_derived_klines_from_daily(
        self,
        symbol: str,
        period: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> int:
        return len(
            self._list_derived_klines_from_daily(
                symbol,
                period=period,
                limit=None,
                start_date=start_date,
                end_date=end_date,
            )
        )

    def _aggregate_from_daily(self, rows: list[dict], period: str) -> list[dict]:
        normalized_period = self._normalize_period(period)
        if not rows or normalized_period not in {"weekly", "monthly"}:
            return rows

        groups: list[dict] = []
        current_key: str | None = None
        current_group: list[dict] = []

        for row in rows:
            trade_time = str(row.get("trade_time") or "")
            try:
                dt = datetime.fromisoformat(trade_time)
            except ValueError:
                dt = datetime.strptime(trade_time[:10], "%Y-%m-%d")
            if normalized_period == "weekly":
                iso_year, iso_week, _ = dt.isocalendar()
                group_key = f"{iso_year}-W{iso_week:02d}"
            else:
                group_key = f"{dt.year:04d}-{dt.month:02d}"

            if current_key is None or group_key == current_key:
                current_key = group_key
                current_group.append(row)
                continue

            groups.append(self._build_aggregated_kline(current_group, normalized_period))
            current_key = group_key
            current_group = [row]

        if current_group:
            groups.append(self._build_aggregated_kline(current_group, normalized_period))
        return groups

    def _build_aggregated_kline(self, rows: list[dict], period: str) -> dict:
        first = rows[0]
        last = rows[-1]
        highs = [self._to_float(row.get("high")) for row in rows]
        lows = [self._to_float(row.get("low")) for row in rows]
        volumes = [self._to_float(row.get("volume")) or 0.0 for row in rows]
        amounts = [self._to_float(row.get("amount")) or 0.0 for row in rows]
        turnover_rates = [self._to_float(row.get("turnover_rate")) or 0.0 for row in rows]
        open_price = self._to_float(first.get("open"))
        close_price = self._to_float(last.get("close"))
        valid_highs = [value for value in highs if value is not None]
        valid_lows = [value for value in lows if value is not None]
        change_pct = None
        if open_price not in (None, 0) and close_price is not None:
            change_pct = round((close_price - open_price) / open_price * 100, 4)

        return {
            "symbol": first.get("symbol"),
            "period": period,
            "trade_time": last.get("trade_time"),
            "open": open_price,
            "high": max(valid_highs) if valid_highs else None,
            "low": min(valid_lows) if valid_lows else None,
            "close": close_price,
            "volume": round(sum(volumes), 4) if volumes else None,
            "amount": round(sum(amounts), 4) if amounts else None,
            "change_pct": change_pct,
            "turnover_rate": round(sum(turnover_rates), 4) if turnover_rates else None,
            "source": "local_aggregated",
            "created_at": last.get("created_at"),
            "updated_at": last.get("updated_at"),
            "created_by": last.get("created_by"),
            "updated_by": last.get("updated_by"),
        }

    def _ensure_local_base_klines(self, symbol: str, period: str) -> None:
        base_period = "daily" if self._is_derived_period(period) else period
        if self._count_klines_from_db(symbol, period=base_period) > 0:
            return
        try:
            self.sync_symbol_kline(symbol, period=base_period, tracked=False)
        except Exception:
            return

    def _fetch_kline_with_retry(self, symbol: str, period: str) -> dict:
        errors: list[str] = []
        attempts = max(1, settings.kline_sync_retry_count)
        for attempt in range(1, attempts + 1):
            try:
                rows = self._fetch_kline(symbol, period)
                if not rows:
                    raise ValueError("外部 K 线接口返回空数据")
                return {
                    "rows": rows,
                    "source": self._detect_rows_source(rows),
                    "attempts": attempt,
                    "errors": errors,
                }
            except Exception as exc:
                errors.append(str(exc))
                if attempt < attempts:
                    time.sleep(settings.kline_sync_retry_delay_seconds * (2 ** (attempt - 1)))
        raise RuntimeError("；".join(errors) or "外部 K 线接口同步失败")

    def _detect_rows_source(self, rows: list[dict]) -> str:
        for row in rows:
            source = row.get("source")
            if source:
                return str(source)
        return "external_provider"

    def _fetch_kline(self, symbol: str, period: str) -> list[dict]:
        normalized_period = self._normalize_period(period)
        if normalized_period == "daily":
            now = datetime.now()
            start_date = (now - timedelta(days=365 * 3)).strftime("%Y%m%d")
            end_date = now.strftime("%Y%m%d")
            return self.market_data.get_daily_kline(symbol, start_date=start_date, end_date=end_date)
        if normalized_period in {"weekly", "monthly"}:
            now = datetime.now()
            start_date = (now - timedelta(days=365 * 3)).strftime("%Y%m%d")
            end_date = now.strftime("%Y%m%d")
            rows = self.market_data.get_daily_kline(symbol, start_date=start_date, end_date=end_date)
            normalized_rows = [
                {
                    "symbol": self._normalize_symbol(symbol),
                    "period": "daily",
                    "trade_time": str(row.get("trade_time") or row.get("date") or row.get("datetime")),
                    "open": self._to_float(row.get("open")),
                    "high": self._to_float(row.get("high")),
                    "low": self._to_float(row.get("low")),
                    "close": self._to_float(row.get("close")),
                    "volume": self._to_float(row.get("volume")),
                    "amount": self._to_float(row.get("amount")),
                    "change_pct": self._to_float(row.get("change_pct")),
                    "turnover_rate": self._to_float(row.get("turnover_rate")),
                    "source": row.get("source") or "external_provider",
                    "created_at": None,
                    "updated_at": None,
                    "created_by": None,
                    "updated_by": None,
                }
                for row in rows
                if row.get("trade_time") or row.get("date") or row.get("datetime")
            ]
            return self._aggregate_from_daily(normalized_rows, normalized_period)
        if normalized_period == "minute":
            return self.market_data.get_minute_kline(symbol, period="5")
        raise ValueError(f"不支持的 K 线周期：{period}")

    def _log_sync(self, pool_code: str | None, symbol: str, period: str, status: str, rows_count: int, message: str, **_: Any) -> None:
        now = self._now()
        with SessionLocal() as session:
            session.add(
                KlineSyncLogModel(
                    pool_code=pool_code,
                    symbol=symbol,
                    period=period,
                    status=status,
                    rows_count=rows_count,
                    message=message,
                    created_at=now,
                    updated_at=now,
                    created_by="system",
                    updated_by="system",
                )
            )
            session.commit()

    def _kline_to_dict(self, row: StockKlineModel) -> dict:
        return {
            "id": row.id,
            "symbol": row.symbol,
            "period": row.period,
            "trade_time": row.trade_time,
            "open": row.open,
            "high": row.high,
            "low": row.low,
            "close": row.close,
            "volume": row.volume,
            "amount": row.amount,
            "change_pct": row.change_pct,
            "turnover_rate": row.turnover_rate,
            "source": row.source,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "created_by": row.created_by,
            "updated_by": row.updated_by,
        }

    def _normalize_symbol(self, symbol: str) -> str:
        return symbol.strip().upper().split(".")[0]

    def _is_stock_symbol(self, symbol: str) -> bool:
        normalized = self._normalize_symbol(symbol)
        return len(normalized) == 6 and normalized.isdigit() and not normalized.startswith("88")

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _to_float(self, value: Any) -> float | None:
        if value in (None, "", "-"):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _build_local_ready_result(self, symbol: str, period: str, rows_count: int) -> dict:
        return {
            "symbol": self._normalize_symbol(symbol),
            "period": period,
            "status": "success",
            "rows_count": int(rows_count),
            "source": "local_cache_or_db",
            "cache_enabled": self.kline_cache.enabled,
            "fallback_used": False,
            "attempts": 0,
            "message": "本地已有足够 K 线，跳过外部同步",
        }

    def _build_synthetic_klines(self, symbol: str, period: str, limit: int = 120) -> list[dict]:
        base_rows = self.market_data.get_kline(symbol, days=max(limit, 60))
        normalized_rows = [
            {
                "symbol": self._normalize_symbol(symbol),
                "period": "daily",
                "trade_time": str(row.get("trade_time") or row.get("date") or ""),
                "open": self._to_float(row.get("open")),
                "high": self._to_float(row.get("high")),
                "low": self._to_float(row.get("low")),
                "close": self._to_float(row.get("close")),
                "volume": self._to_float(row.get("volume")),
                "amount": self._to_float(row.get("amount")),
                "change_pct": self._to_float(row.get("change_pct")),
                "turnover_rate": self._to_float(row.get("turnover_rate")),
                "source": "local_snapshot_fallback",
                "created_at": None,
                "updated_at": None,
                "created_by": None,
                "updated_by": None,
            }
            for row in base_rows
            if row.get("trade_time") or row.get("date")
        ]
        normalized_period = self._normalize_period(period)
        if self._is_derived_period(normalized_period):
            return self._aggregate_from_daily(normalized_rows, normalized_period)[-limit:]
        return normalized_rows[-limit:]

    def _normalize_period(self, period: str) -> str:
        value = str(period or "daily").strip().lower()
        aliases = {
            "day": "daily",
            "daily": "daily",
            "week": "weekly",
            "weekly": "weekly",
            "month": "monthly",
            "monthly": "monthly",
            "min": "minute",
            "minute": "minute",
            "5m": "minute",
        }
        return aliases.get(value, value)

    def _is_derived_period(self, period: str) -> bool:
        return period in {"weekly", "monthly"}
