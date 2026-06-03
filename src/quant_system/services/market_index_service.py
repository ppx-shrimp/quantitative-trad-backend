from __future__ import annotations

import json
import os
import re
import socket
import ssl
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from quant_system.core.config import settings
from quant_system.db.database import SessionLocal
from quant_system.db.models import StockBasicModel


EASTMONEY_INDEX_URLS = [
    "https://push2.eastmoney.com/api/qt/clist/get",
    "http://push2.eastmoney.com/api/qt/clist/get",
    "https://push2his.eastmoney.com/api/qt/clist/get",
]
INDEX_DEFINITIONS = [
    {"code": "000001", "ts_code": "000001.SH", "name": "上证指数", "short_name": "上证", "secid": "1.000001", "tencent": "sh000001", "sina": "sh000001"},
    {"code": "399001", "ts_code": "399001.SZ", "name": "深证成指", "short_name": "深证", "secid": "0.399001", "tencent": "sz399001", "sina": "sz399001"},
    {"code": "399006", "ts_code": "399006.SZ", "name": "创业板指", "short_name": "创业", "secid": "0.399006", "tencent": "sz399006", "sina": "sz399006"},
    {"code": "000688", "ts_code": "000688.SH", "name": "科创50", "short_name": "科创50", "secid": "1.000688", "tencent": "sh000688", "sina": "sh000688"},
]


class MarketIndexService:
    def __init__(self) -> None:
        self.cache_path = Path(settings.cache_dir) / "market_indices.json"
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

    def get_indices(self, force_refresh: bool = False) -> dict:
        cached = self._read_cache()
        if cached and not force_refresh and self._is_fresh(cached, max_age_seconds=60):
            return {**cached, "source": cached.get("source") or "eastmoney_cache", "cache_hit": True}

        try:
            items = self._fetch_indices_with_fallbacks()
            source = str(items[0].get("source") or "market_index") if items else "market_index"
            payload = {
                "items": items,
                "count": len(items),
                "source": source,
                "cache_hit": False,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "message": f"指数行情来自 {source}。",
            }
            self._write_cache(payload)
            return payload
        except Exception as exc:
            if cached:
                return {
                    **cached,
                    "source": "eastmoney_cache",
                    "cache_hit": True,
                    "stale": True,
                    "message": f"实时指数源暂不可用，已使用最近一次缓存：{exc}",
                }
            return {
                "items": [],
                "count": 0,
                "source": "unavailable",
                "cache_hit": False,
                "stale": True,
                "fetched_at": None,
                "message": f"指数行情暂不可用：{exc}",
            }

    def get_hot_sectors(self, limit: int = 8, force_refresh: bool = False) -> dict:
        cached = self._read_named_cache("hot_sectors")
        if cached and not force_refresh and self._is_fresh(cached, max_age_seconds=300):
            return {**cached, "cache_hit": True}
        try:
            items = self._fetch_hot_sectors_from_eastmoney(limit=limit)
            payload = {
                "items": items,
                "count": len(items),
                "source": "eastmoney_sector",
                "cache_hit": False,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "message": "热门板块来自东方财富板块实时行情。",
            }
            self._write_named_cache("hot_sectors", payload)
            return payload
        except Exception as exc:
            if cached:
                return {**cached, "cache_hit": True, "stale": True, "message": f"实时板块源暂不可用，已使用最近一次缓存：{exc}"}
            items = self._derive_hot_sectors_from_stock_basic(limit=limit)
            return {
                "items": items,
                "count": len(items),
                "source": "local_stock_basic_industry",
                "cache_hit": False,
                "stale": True,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "message": f"实时板块源暂不可用，已按本地股票基础表行业分布兜底：{exc}",
            }

    def diagnose_connectivity(self) -> dict:
        hosts = ["push2.eastmoney.com", "push2his.eastmoney.com", "qt.gtimg.cn", "hq.sinajs.cn", "www.baidu.com"]
        env_proxy_keys = ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy"]
        diagnostics: dict[str, Any] = {
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "proxy_env": {key: self._mask_proxy(os.environ.get(key)) for key in env_proxy_keys if os.environ.get(key)},
            "hosts": {},
            "http": [],
            "suggestion": "如果 DNS/TCP/TLS 均失败，优先检查出站防火墙/代理；入站规则通常不影响后端主动访问外部行情源。",
        }
        for host in hosts:
            diagnostics["hosts"][host] = self._diagnose_host(host)
        for url in EASTMONEY_INDEX_URLS + ["https://qt.gtimg.cn/q=sh000001", "https://hq.sinajs.cn/list=sh000001", "https://www.baidu.com"]:
            diagnostics["http"].append(self._diagnose_http(url))
        return diagnostics

    def _fetch_indices_with_fallbacks(self) -> list[dict]:
        errors: list[str] = []
        for label, fetcher in (
            ("eastmoney", self._fetch_indices),
            ("tencent", self._fetch_indices_from_tencent),
            ("sina", self._fetch_indices_from_sina),
            ("akshare_spot", self._fetch_indices_from_akshare_spot),
            ("akshare_hist", self._fetch_indices_from_akshare_hist),
        ):
            try:
                items = fetcher()
                if items:
                    return items
            except Exception as exc:
                errors.append(f"{label}: {exc}")
        raise RuntimeError("；".join(errors))

    def _fetch_indices(self) -> list[dict]:
        params = {
            "pn": 1,
            "pz": len(INDEX_DEFINITIONS),
            "po": "1",
            "np": "1",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": "2",
            "invt": "2",
            "fid": "f12",
            "fs": ",".join(f"i:{item['secid']}" for item in INDEX_DEFINITIONS),
            "fields": "f2,f3,f4,f12,f14,f17,f18",
        }
        payload = self._request_index_payload(params)
        rows = (payload.get("data") or {}).get("diff") or []
        by_code = {str(row.get("f12") or "").zfill(6): row for row in rows if isinstance(row, dict)}
        items = []
        for definition in INDEX_DEFINITIONS:
            row = by_code.get(definition["code"])
            if not row:
                continue
            price = self._to_float(row.get("f2"))
            pct = self._to_float(row.get("f3"))
            change = self._to_float(row.get("f4"))
            pre_close = self._to_float(row.get("f18"))
            open_price = self._to_float(row.get("f17"))
            items.append(self._build_item(definition, price, change, pct, open_price, pre_close, source="eastmoney"))
        if not items:
            raise RuntimeError("东方财富未返回可用指数行情。")
        return items

    def _fetch_indices_from_tencent(self) -> list[dict]:
        symbols = ",".join(definition["tencent"] for definition in INDEX_DEFINITIONS)
        url = f"https://qt.gtimg.cn/q={symbols}"
        response = self.session.get(url, timeout=(3, 8), proxies={"http": None, "https": None})
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "gbk"
        text = response.text
        by_symbol: dict[str, list[str]] = {}
        for match in re.finditer(r'v_([^=]+)="([^"]*)";', text):
            by_symbol[match.group(1)] = match.group(2).split("~")
        items = []
        for definition in INDEX_DEFINITIONS:
            values = by_symbol.get(definition["tencent"])
            if not values or len(values) < 33:
                continue
            price = self._to_float(values[3])
            pre_close = self._to_float(values[4])
            open_price = self._to_float(values[5])
            change = self._to_float(values[31])
            pct = self._to_float(values[32])
            items.append(self._build_item(definition, price, change, pct, open_price, pre_close, source="tencent"))
        if not items:
            raise RuntimeError("腾讯行情接口未返回目标指数。")
        return items

    def _fetch_indices_from_sina(self) -> list[dict]:
        symbols = ",".join(definition["sina"] for definition in INDEX_DEFINITIONS)
        url = f"https://hq.sinajs.cn/list={symbols}"
        headers = {"Referer": "https://finance.sina.com.cn/", "User-Agent": self.session.headers.get("User-Agent", "Mozilla/5.0")}
        response = self.session.get(url, timeout=(3, 8), proxies={"http": None, "https": None}, headers=headers)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "gbk"
        text = response.text
        by_symbol: dict[str, list[str]] = {}
        for match in re.finditer(r'var hq_str_([^=]+)="([^"]*)";', text):
            by_symbol[match.group(1)] = match.group(2).split(",")
        items = []
        for definition in INDEX_DEFINITIONS:
            values = by_symbol.get(definition["sina"])
            if not values or len(values) < 4:
                continue
            price = self._to_float(values[1])
            change = self._to_float(values[2])
            pct = self._to_float(values[3])
            pre_close = round(price - change, 2) if price is not None and change is not None else None
            items.append(self._build_item(definition, price, change, pct, None, pre_close, source="sina"))
        if not items:
            raise RuntimeError("新浪行情接口未返回目标指数。")
        return items

    def _fetch_indices_from_akshare_spot(self) -> list[dict]:
        import akshare as ak

        df = ak.stock_zh_index_spot_em()
        rows = self._df_records(df)
        by_code = {str(row.get("代码") or row.get("code") or "").zfill(6): row for row in rows}
        items = []
        for definition in INDEX_DEFINITIONS:
            row = by_code.get(definition["code"])
            if not row:
                continue
            price = self._to_float(row.get("最新价") or row.get("price"))
            pct = self._to_float(row.get("涨跌幅") or row.get("change_pct"))
            change = self._to_float(row.get("涨跌额") or row.get("change"))
            pre_close = self._to_float(row.get("昨收") or row.get("pre_close"))
            open_price = self._to_float(row.get("今开") or row.get("open"))
            items.append(self._build_item(definition, price, change, pct, open_price, pre_close, source="akshare_spot"))
        if not items:
            raise RuntimeError("AkShare 指数实时接口未返回目标指数。")
        return items

    def _fetch_indices_from_akshare_hist(self) -> list[dict]:
        import akshare as ak

        items = []
        for definition in INDEX_DEFINITIONS:
            try:
                df = ak.stock_zh_index_daily_em(symbol=definition["code"])
            except TypeError:
                df = ak.stock_zh_index_daily(symbol=definition["code"])
            rows = self._df_records(df)
            if not rows:
                continue
            latest = rows[-1]
            previous = rows[-2] if len(rows) >= 2 else {}
            price = self._to_float(latest.get("close") or latest.get("收盘"))
            open_price = self._to_float(latest.get("open") or latest.get("开盘"))
            pre_close = self._to_float(previous.get("close") or previous.get("收盘"))
            change = round(price - pre_close, 2) if price is not None and pre_close not in (None, 0) else None
            pct = round(change / pre_close * 100, 2) if change is not None and pre_close not in (None, 0) else None
            items.append(self._build_item(definition, price, change, pct, open_price, pre_close, source="akshare_hist"))
        if not items:
            raise RuntimeError("AkShare 指数历史接口未返回目标指数。")
        return items

    def _build_item(
        self,
        definition: dict,
        price: float | None,
        change: float | None,
        pct: float | None,
        open_price: float | None,
        pre_close: float | None,
        source: str,
    ) -> dict:
        return {
            **definition,
            "price": price,
            "change": change,
            "pct": pct,
            "open": open_price,
            "pre_close": pre_close,
            "quote_time": datetime.now(timezone.utc).isoformat(),
            "source": source,
        }

    def _df_records(self, df: Any) -> list[dict]:
        if df is None:
            return []
        try:
            cleaned = df.astype(object).where(df.notna(), None)
            return cleaned.to_dict("records")
        except Exception:
            return []

    def _fetch_hot_sectors_from_eastmoney(self, limit: int) -> list[dict]:
        params = {
            "pn": 1,
            "pz": max(limit, 8),
            "po": "1",
            "np": "1",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": "2",
            "invt": "2",
            "fid": "f3",
            "fs": "m:90+t:2",
            "fields": "f2,f3,f4,f12,f14,f20,f21,f62",
        }
        payload = self._request_index_payload(params)
        rows = (payload.get("data") or {}).get("diff") or []
        items = []
        for row in rows[:limit]:
            if not isinstance(row, dict):
                continue
            items.append(
                {
                    "code": row.get("f12") or "",
                    "name": row.get("f14") or "-",
                    "price": self._to_float(row.get("f2")),
                    "change": self._to_float(row.get("f4")),
                    "pct": self._to_float(row.get("f3")),
                    "amount": self._to_float(row.get("f20")),
                    "net_flow": self._to_float(row.get("f62")),
                    "source": "eastmoney_sector",
                }
            )
        if not items:
            raise RuntimeError("东方财富未返回可用板块行情。")
        return items

    def _derive_hot_sectors_from_stock_basic(self, limit: int) -> list[dict]:
        with SessionLocal() as session:
            rows = session.query(StockBasicModel.industry).filter(StockBasicModel.industry.isnot(None), StockBasicModel.industry != "").all()
        counts: dict[str, int] = {}
        for (industry,) in rows:
            name = str(industry or "").strip()
            if name:
                counts[name] = counts.get(name, 0) + 1
        return [
            {"code": "", "name": name, "price": None, "change": None, "pct": None, "stock_count": count, "source": "local_stock_basic_industry"}
            for name, count in sorted(counts.items(), key=lambda item: item[1], reverse=True)[:limit]
        ]

    def _request_index_payload(self, params: dict) -> dict:
        errors: list[str] = []
        timeout = (min(settings.eastmoney_connect_timeout_seconds, 3.0), min(settings.eastmoney_read_timeout_seconds, 8.0))
        for url in EASTMONEY_INDEX_URLS:
            try:
                response = self.session.get(url, params=params, timeout=timeout, proxies={"http": None, "https": None})
                response.raise_for_status()
                return response.json()
            except Exception as exc:
                errors.append(f"{url}: {exc}")
        raise RuntimeError("；".join(errors))

    def _read_cache(self) -> dict | None:
        return self._read_cache_path(self.cache_path)

    def _write_cache(self, payload: dict) -> None:
        self._write_cache_path(self.cache_path, payload)

    def _read_named_cache(self, name: str) -> dict | None:
        return self._read_cache_path(Path(settings.cache_dir) / f"{name}.json")

    def _write_named_cache(self, name: str, payload: dict) -> None:
        self._write_cache_path(Path(settings.cache_dir) / f"{name}.json", payload)

    def _read_cache_path(self, path: Path) -> dict | None:
        try:
            if not path.exists():
                return None
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) and isinstance(data.get("items"), list) else None
        except Exception:
            return None

    def _write_cache_path(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")

    def _is_fresh(self, payload: dict, max_age_seconds: int) -> bool:
        fetched_at = payload.get("fetched_at")
        if not fetched_at:
            return False
        try:
            fetched = datetime.fromisoformat(str(fetched_at).replace("Z", "+00:00"))
            if fetched.tzinfo is None:
                fetched = fetched.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - fetched).total_seconds() <= max_age_seconds
        except Exception:
            return False

    def _diagnose_host(self, host: str) -> dict:
        result: dict[str, Any] = {"dns": None, "tcp_443": None, "tls": None}
        try:
            infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            addresses = list(dict.fromkeys(info[4][0] for info in infos))
            result["dns"] = {"ok": True, "addresses": addresses[:5]}
        except Exception as exc:
            result["dns"] = {"ok": False, "error": str(exc)}
            return result

        try:
            with socket.create_connection((host, 443), timeout=5):
                result["tcp_443"] = {"ok": True}
        except Exception as exc:
            result["tcp_443"] = {"ok": False, "error": str(exc)}
            return result

        try:
            context = ssl.create_default_context()
            with socket.create_connection((host, 443), timeout=5) as sock:
                with context.wrap_socket(sock, server_hostname=host) as tls_sock:
                    cert = tls_sock.getpeercert()
                    result["tls"] = {"ok": True, "version": tls_sock.version(), "issuer": cert.get("issuer")}
        except Exception as exc:
            result["tls"] = {"ok": False, "error": str(exc)}
        return result

    def _diagnose_http(self, url: str) -> dict:
        try:
            response = self.session.get(url, timeout=(3, 8), proxies={"http": None, "https": None}, stream=True)
            response.close()
            return {"url": url, "ok": True, "status_code": response.status_code, "server": response.headers.get("server")}
        except Exception as exc:
            return {"url": url, "ok": False, "error": str(exc), "error_type": type(exc).__name__}

    def _mask_proxy(self, value: str | None) -> str | None:
        if not value:
            return value
        if "://" not in value or "@" not in value:
            return value
        scheme, rest = value.split("://", 1)
        _credentials, host = rest.split("@", 1)
        return f"{scheme}://***@{host}"

    def _to_float(self, value: Any) -> float | None:
        try:
            if value in (None, "-", ""):
                return None
            return float(value)
        except (TypeError, ValueError):
            return None
