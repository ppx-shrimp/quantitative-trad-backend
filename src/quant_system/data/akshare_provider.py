from __future__ import annotations

import hashlib
import json
from pathlib import Path

from quant_system.core.config import settings
from quant_system.data.eastmoney_provider import EastMoneyMarketDataProvider


def _ensure_cache_dir() -> Path:
    cache_dir = Path(settings.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _cache_path(cache_dir: Path, name: str) -> Path:
    return cache_dir / f"{name}.json"


def _normalize_symbol(value: object) -> str:
    symbol = str(value or "").strip().upper().split(".")[0]
    return symbol if symbol.isdigit() and len(symbol) == 6 else ""


def _exchange_for_symbol(symbol: str) -> str:
    if symbol.startswith(("6", "9")):
        return "SH"
    if symbol.startswith(("0", "2", "3")):
        return "SZ"
    if symbol.startswith(("4", "8")):
        return "BJ"
    return ""


def _market_for_symbol(symbol: str) -> str:
    if symbol.startswith("688"):
        return "科创板"
    if symbol.startswith("300"):
        return "创业板"
    if symbol.startswith(("8", "4")):
        return "北交所"
    if symbol.startswith(("600", "601", "603", "605", "000", "001", "002", "003")):
        return "主板"
    return "-"


def _is_valid_stock_row(row: dict) -> bool:
    symbol = _normalize_symbol(row.get("symbol") or row.get("code") or row.get("ts_code"))
    return bool(symbol and row.get("name"))


def _clean_stock_rows(rows: list[dict]) -> list[dict]:
    cleaned: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = _normalize_symbol(row.get("symbol") or row.get("code") or row.get("ts_code"))
        name = str(row.get("name") or "").strip()
        if not symbol or not name:
            continue
        exchange = _exchange_for_symbol(symbol)
        normalized = {**row, "symbol": symbol, "code": symbol, "ts_code": f"{symbol}.{exchange}" if exchange else symbol, "name": name}
        normalized.setdefault("market", _market_for_symbol(symbol))
        cleaned[symbol] = normalized
    return list(cleaned.values())


def _read_cache(path: Path, max_age_seconds: int = 6 * 60 * 60) -> list[dict] | None:
    if not path.exists():
        return None
    if max_age_seconds is not None:
        import time
        age = time.time() - path.stat().st_mtime
        if age > max_age_seconds:
            return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return None
        if path.name == "stock_list.json":
            cleaned = _clean_stock_rows(data)
            return cleaned or None
        return data
    except Exception:
        return None


def _write_cache(path: Path, rows: list[dict]) -> None:
    path.write_text(json.dumps(rows, ensure_ascii=False, default=str), encoding="utf-8")


def _allow_market_data_file_cache(name: str) -> bool:
    if name == "stock_list":
        return True
    return bool(settings.market_data_file_cache_enabled)


def _hash_name(prefix: str, value: str) -> str:
    return f"{prefix}_{hashlib.md5(value.encode('utf-8')).hexdigest()}"


def _get_akshare():
    """按需导入 akshare，避免后端启动阶段被重型三方库导入卡住。"""
    import akshare as ak

    return ak


class AkShareMarketDataProvider:
    """基于 AkShare 的 A 股数据提供者。

    优先返回缓存数据，避免频繁请求外部接口。
    """

    def get_stock_list(self) -> list[dict]:
        cache_dir = _ensure_cache_dir()
        cache_path = _cache_path(cache_dir, "stock_list")
        cached = _read_cache(cache_path, max_age_seconds=12 * 60 * 60) if _allow_market_data_file_cache("stock_list") else None
        if cached is not None:
            return cached
        rows = EastMoneyMarketDataProvider().get_stock_list(force_refresh=True)
        if _allow_market_data_file_cache("stock_list"):
            _write_cache(cache_path, rows)
        return rows

    def get_daily_kline(self, symbol: str, start_date: str | None = None, end_date: str | None = None) -> list[dict]:
        cache_dir = _ensure_cache_dir()
        name = _hash_name("daily", f"{symbol}_{start_date}_{end_date}")
        cache_path = _cache_path(cache_dir, name)
        cached = _read_cache(cache_path, max_age_seconds=6 * 60 * 60) if _allow_market_data_file_cache("daily") else None
        if cached is not None:
            return cached
        try:
            ak = _get_akshare()
            df = ak.stock_zh_a_hist(symbol=symbol, period="daily", start_date=start_date, end_date=end_date, adjust="qfq")
            df = df.rename(columns={
                "日期": "date",
                "开盘": "open",
                "收盘": "close",
                "最高": "high",
                "最低": "low",
                "成交量": "volume",
                "成交额": "amount",
                "涨跌幅": "change_pct",
                "换手率": "turnover_rate",
            })
            columns = [c for c in ["date", "open", "close", "high", "low", "volume", "amount", "change_pct", "turnover_rate"] if c in df.columns]
            rows = df[columns].astype(object).where(df[columns].notna(), None).to_dict(orient="records")
        except Exception:
            rows = EastMoneyMarketDataProvider().get_daily_kline(symbol=symbol, start_date=start_date, end_date=end_date)
        if _allow_market_data_file_cache("daily"):
            _write_cache(cache_path, rows)
        return rows

    def get_minute_kline(self, symbol: str, period: str = "5") -> list[dict]:
        cache_dir = _ensure_cache_dir()
        name = _hash_name("minute", f"{symbol}_{period}")
        cache_path = _cache_path(cache_dir, name)
        cached = _read_cache(cache_path, max_age_seconds=2 * 60 * 60) if _allow_market_data_file_cache("minute") else None
        if cached is not None:
            return cached
        try:
            ak = _get_akshare()
            df = ak.stock_zh_a_hist_min_em(symbol=symbol, period=period, adjust="qfq")
            df = df.rename(columns={
                "时间": "datetime",
                "开盘": "open",
                "收盘": "close",
                "最高": "high",
                "最低": "low",
                "成交量": "volume",
                "成交额": "amount",
            })
            columns = [c for c in ["datetime", "open", "close", "high", "low", "volume", "amount"] if c in df.columns]
            rows = df[columns].astype(object).where(df[columns].notna(), None).to_dict(orient="records")
        except Exception:
            rows = EastMoneyMarketDataProvider().get_minute_kline(symbol=symbol, period=period)
        if _allow_market_data_file_cache("minute"):
            _write_cache(cache_path, rows)
        return rows

    def get_latest_snapshot(self, symbol: str) -> dict:
        rows = self.get_stock_list()
        target_symbol = symbol.split(".")[0]
        for row in rows:
            if str(row.get("symbol")) == target_symbol:
                return row
        return {"symbol": target_symbol, "price": None, "change_pct": None, "volume": None}
