from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any

import requests

from quant_system.core.config import settings


EASTMONEY_STOCK_LIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
EASTMONEY_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
EASTMONEY_FULL_A_SHARE_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048,m:0+t:82+s:2048,m:1+t:3"
EASTMONEY_STOCK_FIELDS = "f2,f3,f5,f6,f12,f14,f15,f16,f17,f18,f100,f102,f104"

FALLBACK_STOCK_ROWS = [
    {"symbol": "600487", "name": "亨通光电", "industry": "通信设备", "area": "江苏"},
    {"symbol": "600519", "name": "贵州茅台", "industry": "白酒", "area": "贵州"},
    {"symbol": "000001", "name": "平安银行", "industry": "银行", "area": "深圳"},
    {"symbol": "000858", "name": "五粮液", "industry": "白酒", "area": "四川"},
    {"symbol": "300750", "name": "宁德时代", "industry": "电池", "area": "福建"},
    {"symbol": "601318", "name": "中国平安", "industry": "保险", "area": "深圳"},
    {"symbol": "600036", "name": "招商银行", "industry": "银行", "area": "深圳"},
    {"symbol": "601398", "name": "工商银行", "industry": "银行", "area": "北京"},
    {"symbol": "002594", "name": "比亚迪", "industry": "汽车", "area": "深圳"},
    {"symbol": "300059", "name": "东方财富", "industry": "证券", "area": "上海"},
]


class EastMoneyProviderError(RuntimeError):
    pass


def _ensure_cache_dir() -> Path:
    cache_dir = Path(settings.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _cache_path(name: str) -> Path:
    return _ensure_cache_dir() / f"{name}.json"


def _normalize_symbol(value: Any) -> str:
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


def _status_for_value(value: Any) -> bool:
    text = str(value or "").strip()
    return text not in {"停牌", "退市", "暂停上市", "0", "false", "False"}


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
        normalized.setdefault("exchange", exchange)
        normalized.setdefault("market", _market_for_symbol(symbol))
        normalized.setdefault("area", "")
        normalized.setdefault("industry", "")
        normalized.setdefault("list_date", "")
        normalized.setdefault("is_active", _status_for_value(normalized.get("status")))
        cleaned[symbol] = normalized
    return list(cleaned.values())


def _read_cache(name: str) -> list[dict] | None:
    path = _cache_path(name)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return None
        if name == "stock_list":
            cleaned = _clean_stock_rows(data)
            return cleaned or None
        return data
    except Exception:
        return None


def _write_cache(name: str, rows: list[dict]) -> None:
    _cache_path(name).write_text(json.dumps(rows, ensure_ascii=False, default=str), encoding="utf-8")


def _allow_market_data_file_cache(name: str) -> bool:
    if name == "stock_list":
        return True
    return bool(settings.market_data_file_cache_enabled)


def _merge_fallback_rows(rows: list[dict]) -> list[dict]:
    merged = {row["symbol"]: row for row in _clean_stock_rows(rows)}
    for row in _clean_stock_rows(FALLBACK_STOCK_ROWS):
        merged.setdefault(row["symbol"], row)
    return list(merged.values())


def _to_float(value: Any) -> float | None:
    if value in (None, "-", ""):
        return None
    try:
        number = float(value)
        if math.isnan(number):
            return None
        return number
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    if value in (None, "-", ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


class EastMoneyMarketDataProvider:
    """东方财富 A 股股票列表兜底 provider。

    AkShare 的股票列表接口底层同样依赖东方财富。这里直接请求东方财富接口，
    增加浏览器请求头、超时和可控异常，避免上层 API 直接返回 500 堆栈。
    """

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
                "Referer": "https://quote.eastmoney.com/center/gridlist.html",
                "Accept": "application/json,text/plain,*/*",
            }
        )

    def get_stock_list(self, force_refresh: bool = False) -> list[dict]:
        if not force_refresh:
            cached = _read_cache("stock_list") if _allow_market_data_file_cache("stock_list") else None
            if cached:
                rows = _merge_fallback_rows(cached)
                if _allow_market_data_file_cache("stock_list"):
                    _write_cache("stock_list", rows)
                return rows
        rows = _merge_fallback_rows(self._fetch_stock_list())
        if len(rows) < 5200:
            cached = _read_cache("stock_list") if _allow_market_data_file_cache("stock_list") else None
            if cached and len(cached) > len(rows):
                rows = _merge_fallback_rows(cached)
            elif len(rows) <= len(_clean_stock_rows(FALLBACK_STOCK_ROWS)):
                raise EastMoneyProviderError(f"股票列表数量异常，仅获取到 {len(rows)} 条，请检查外部数据源。")
        if _allow_market_data_file_cache("stock_list"):
            _write_cache("stock_list", rows)
        return rows

    def get_daily_kline(self, symbol: str, start_date: str | None = None, end_date: str | None = None) -> list[dict]:
        secid = self._to_secid(symbol)
        params = {
            "secid": secid,
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": "101",
            "fqt": "1",
            "beg": start_date or "19900101",
            "end": end_date or "20500101",
            "lmt": "1000000",
        }
        payload = self._request_json_with_retry(EASTMONEY_KLINE_URL, params=params, label="东方财富日 K 接口")

        data = payload.get("data") or {}
        klines = data.get("klines") or []
        rows = [self._normalize_kline_row(item) for item in klines]
        if not rows:
            raise EastMoneyProviderError(f"东方财富未返回 {symbol} 的日 K 数据。")
        return rows

    def get_minute_kline(self, symbol: str, period: str = "5") -> list[dict]:
        normalized_period = str(period or "5").strip()
        if normalized_period not in {"1", "5", "15", "30", "60"}:
            raise EastMoneyProviderError(f"东方财富不支持的分钟 K 周期：{period}")
        secid = self._to_secid(symbol)
        params = {
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "ut": "7eea3edcaed734bea9cbfc24409ed989",
            "klt": normalized_period,
            "fqt": "1",
            "secid": secid,
            "beg": "0",
            "end": "20500000",
        }
        payload = self._request_json_with_retry(EASTMONEY_KLINE_URL, params=params, label="东方财富分钟 K 接口")
        data = payload.get("data") or {}
        klines = data.get("klines") or []
        rows = [self._normalize_minute_kline_row(item) for item in klines]
        if not rows:
            raise EastMoneyProviderError(f"东方财富未返回 {symbol} 的分钟 K 数据。")
        return rows

    def _fetch_stock_list(self) -> list[dict]:
        all_rows: list[dict] = []
        page = 1
        page_size = 200
        total_pages = 1
        while page <= total_pages:
            payload = self._fetch_page(page=page, page_size=page_size)
            data = payload.get("data") or {}
            diff = data.get("diff") or []
            total = data.get("total") or len(diff)
            total_pages = max(1, math.ceil(total / page_size))
            if data.get("normalized"):
                all_rows.extend(row for row in diff if isinstance(row, dict))
            else:
                all_rows.extend(self._normalize_row(row) for row in diff)
            page += 1
        all_rows = _clean_stock_rows(all_rows)
        if not all_rows:
            fallback_rows = _clean_stock_rows(FALLBACK_STOCK_ROWS)
            if fallback_rows:
                return fallback_rows
            raise EastMoneyProviderError("东方财富接口返回为空，未获取到股票列表。")
        return all_rows

    def _fetch_page(self, page: int, page_size: int) -> dict:
        params = {
            "pn": page,
            "pz": page_size,
            "po": "1",
            "np": "1",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": "2",
            "invt": "2",
            "fid": "f12",
            "fs": EASTMONEY_FULL_A_SHARE_FS,
            "fields": EASTMONEY_STOCK_FIELDS,
        }
        try:
            return self._request_json_with_retry(EASTMONEY_STOCK_LIST_URL, params=params, label="东方财富行情接口")
        except EastMoneyProviderError:
            cached = _read_cache("stock_list") if _allow_market_data_file_cache("stock_list") else None
            if cached:
                return {"data": {"diff": cached, "total": len(cached), "normalized": True}}
            raise

    def _request_json_with_retry(self, url: str, params: dict, label: str, retries: int | None = None) -> dict:
        last_error: Exception | None = None
        retries = int(retries or settings.eastmoney_retry_count)
        for attempt in range(1, retries + 1):
            try:
                response = self.session.get(
                    url,
                    params=params,
                    timeout=(settings.eastmoney_connect_timeout_seconds, settings.eastmoney_read_timeout_seconds),
                )
                response.raise_for_status()
                return response.json()
            except requests.RequestException as exc:
                last_error = exc
            except ValueError as exc:
                raise EastMoneyProviderError(f"{label}返回内容不是有效 JSON。") from exc
            if attempt < retries:
                time.sleep(settings.eastmoney_retry_delay_seconds * (2 ** (attempt - 1)))
        raise EastMoneyProviderError(f"无法连接{label}，已重试 {retries} 次：{last_error}")

    def _normalize_row(self, row: dict) -> dict:
        symbol = _normalize_symbol(row.get("f12"))
        exchange = _exchange_for_symbol(symbol)
        return {
            "symbol": symbol,
            "code": symbol,
            "ts_code": f"{symbol}.{exchange}" if exchange else symbol,
            "exchange": exchange,
            "name": row.get("f14"),
            "price": _to_float(row.get("f2")),
            "change_pct": _to_float(row.get("f3")),
            "volume": _to_int(row.get("f5")),
            "amount": _to_float(row.get("f6")),
            "open": _to_float(row.get("f17")),
            "high": _to_float(row.get("f15")),
            "low": _to_float(row.get("f16")),
            "pre_close": _to_float(row.get("f18")),
            "industry": row.get("f100") or "",
            "area": row.get("f102") or "",
            "status": row.get("f104") or "",
            "market": _market_for_symbol(symbol),
            "list_date": "",
            "is_active": _status_for_value(row.get("f104")),
            "source": "eastmoney",
        }

    def _normalize_kline_row(self, item: str) -> dict:
        values = item.split(",")
        return {
            "date": values[0] if len(values) > 0 else None,
            "open": _to_float(values[1]) if len(values) > 1 else None,
            "close": _to_float(values[2]) if len(values) > 2 else None,
            "high": _to_float(values[3]) if len(values) > 3 else None,
            "low": _to_float(values[4]) if len(values) > 4 else None,
            "volume": _to_float(values[5]) if len(values) > 5 else None,
            "amount": _to_float(values[6]) if len(values) > 6 else None,
            "change_pct": _to_float(values[8]) if len(values) > 8 else None,
            "turnover_rate": _to_float(values[10]) if len(values) > 10 else None,
            "source": "eastmoney",
        }

    def _normalize_minute_kline_row(self, item: str) -> dict:
        values = item.split(",")
        return {
            "datetime": values[0] if len(values) > 0 else None,
            "open": _to_float(values[1]) if len(values) > 1 else None,
            "close": _to_float(values[2]) if len(values) > 2 else None,
            "high": _to_float(values[3]) if len(values) > 3 else None,
            "low": _to_float(values[4]) if len(values) > 4 else None,
            "volume": _to_float(values[5]) if len(values) > 5 else None,
            "amount": _to_float(values[6]) if len(values) > 6 else None,
            "change_pct": _to_float(values[8]) if len(values) > 8 else None,
            "turnover_rate": _to_float(values[10]) if len(values) > 10 else None,
            "source": "eastmoney",
        }

    def _to_secid(self, symbol: str) -> str:
        normalized = symbol.strip().upper().split(".")[0]
        if normalized.startswith("6"):
            return f"1.{normalized}"
        return f"0.{normalized}"
