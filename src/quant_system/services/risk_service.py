"""独立风控服务。

架构定位：策略层产生信号 → 风控层独立判断 → broker 层执行。
三层分离确保风控逻辑不会被交易逻辑绕过，也方便后续独立升级风控规则。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from quant_system.core.config import settings

if TYPE_CHECKING:
    from quant_system.brokers.base import TradingBroker


class RiskService:
    """集中管理所有交易前置风控检查。

    所有 check 方法均为无副作用的纯判断，不修改任何状态。
    broker 以参数形式传入，RiskService 自身不持有 broker 引用。
    """

    # ------------------------------------------------------------------
    # 主入口：交易安全检查
    # ------------------------------------------------------------------

    def check_trade_guard(
        self,
        action: str,
        symbol: str,
        quantity: int | None,
        price: float | None,
        broker: TradingBroker | None = None,
    ) -> dict[str, Any]:
        """综合交易风控检查。

        Parameters
        ----------
        action : str
            操作类型，可选值：manual_open / manual_close / auto_buy / auto_close
        symbol : str
            标的代码
        quantity : int | None
            委托数量
        price : float | None
            委托价格
        broker : TradingBroker | None
            当前交易通道实例，用于查询持仓和订单

        Returns
        -------
        dict
            包含 allowed / reason / limits 等字段的风控结果
        """
        normalized_symbol = _normalize_symbol(symbol)
        quantity_value = int(quantity or 0)
        price_value = float(price or 0)
        order_amount = round(quantity_value * price_value, 2) if quantity_value and price_value else 0.0

        reasons: list[str] = []

        # 1. 交易模式检查
        reasons.extend(self._check_trade_mode())

        # 2. 操作权限检查
        reasons.extend(self._check_action_permission(action))

        # 3. 单笔金额检查
        if action in {"manual_open", "auto_buy"} and order_amount > 0:
            reasons.extend(self._check_order_amount(order_amount))

        # 4. 单笔持仓金额检查
        if action in {"manual_open", "auto_buy"} and order_amount > 0:
            reasons.extend(self._check_position_amount(order_amount))

        # 5. 持仓数量检查
        if action in {"manual_open", "auto_buy"} and broker is not None:
            reasons.extend(self._check_position_count(broker))

        # 6. 当日累计买入金额检查
        if action in {"manual_open", "auto_buy"} and broker is not None:
            reasons.extend(self._check_daily_buy_total(broker, order_amount))

        # 7. 交易时段检查（仅警告级，不阻断 paper 模式）
        trading_hours_warning = self._check_trading_hours()

        allowed = not reasons
        return {
            "allowed": allowed,
            "accepted": allowed,
            "action": action,
            "symbol": normalized_symbol,
            "quantity": quantity,
            "price": price_value,
            "order_amount": order_amount,
            "reason": "通过交易安全检查" if not reasons else "；".join(reasons),
            "warnings": trading_hours_warning or [],
            "limits": self._current_limits(),
        }

    # ------------------------------------------------------------------
    # 风控状态（用于健康检查）
    # ------------------------------------------------------------------

    def risk_status(self, broker: TradingBroker | None = None) -> dict[str, Any]:
        """返回当前风控配置和运行状态，供 /health 端点展示。"""
        position_count = broker.position_count() if broker is not None else 0
        daily_used = self._query_daily_buy_total(broker) if broker is not None else 0.0
        return {
            "trade_mode": settings.trade_mode,
            "environment": settings.environment,
            "switches": {
                "allow_live_trading": settings.allow_live_trading,
                "allow_manual_open": settings.allow_manual_open,
                "allow_manual_close": settings.allow_manual_close,
                "allow_auto_buy": settings.allow_auto_buy,
                "allow_auto_close": settings.allow_auto_close,
            },
            "limits": {
                "max_order_amount": settings.max_order_amount,
                "max_daily_buy_amount": settings.max_daily_buy_amount,
                "max_position_amount": settings.max_position_amount,
                "max_positions": settings.auto_trade_max_positions,
            },
            "runtime": {
                "position_count": position_count,
                "position_slots_remaining": max(0, settings.auto_trade_max_positions - position_count),
                "daily_buy_used": round(daily_used, 2),
                "daily_buy_remaining": round(max(0, settings.max_daily_buy_amount - daily_used), 2),
            },
        }

    # ------------------------------------------------------------------
    # 子检查：交易模式
    # ------------------------------------------------------------------

    def _check_trade_mode(self) -> list[str]:
        reasons: list[str] = []
        if settings.trade_mode == "live" and not settings.allow_live_trading:
            reasons.append("当前禁止 live 实盘交易，请显式开启 QUANT_ALLOW_LIVE_TRADING")
        return reasons

    # ------------------------------------------------------------------
    # 子检查：操作权限
    # ------------------------------------------------------------------

    def _check_action_permission(self, action: str) -> list[str]:
        reasons: list[str] = []
        permission_map = {
            "manual_open": ("allow_manual_open", "当前禁止手动开仓，请检查 QUANT_ALLOW_MANUAL_OPEN"),
            "manual_close": ("allow_manual_close", "当前禁止手动平仓，请检查 QUANT_ALLOW_MANUAL_CLOSE"),
            "auto_buy": ("allow_auto_buy", "当前禁止自动买入，请检查 QUANT_ALLOW_AUTO_BUY"),
            "auto_close": ("allow_auto_close", "当前禁止自动平仓，请检查 QUANT_ALLOW_AUTO_CLOSE"),
        }
        entry = permission_map.get(action)
        if entry is not None:
            attr_name, message = entry
            if not getattr(settings, attr_name, True):
                reasons.append(message)
        return reasons

    # ------------------------------------------------------------------
    # 子检查：单笔金额
    # ------------------------------------------------------------------

    def _check_order_amount(self, order_amount: float) -> list[str]:
        reasons: list[str] = []
        if order_amount > settings.max_order_amount:
            reasons.append(
                f"单笔买入金额 {order_amount:.2f} 超过上限 {settings.max_order_amount:.2f}"
            )
        return reasons

    # ------------------------------------------------------------------
    # 子检查：单笔持仓金额
    # ------------------------------------------------------------------

    def _check_position_amount(self, order_amount: float) -> list[str]:
        reasons: list[str] = []
        if order_amount > settings.max_position_amount:
            reasons.append(
                f"单笔持仓金额 {order_amount:.2f} 超过上限 {settings.max_position_amount:.2f}"
            )
        return reasons

    # ------------------------------------------------------------------
    # 子检查：持仓数量
    # ------------------------------------------------------------------

    def _check_position_count(self, broker: TradingBroker) -> list[str]:
        reasons: list[str] = []
        if broker.position_count() >= settings.auto_trade_max_positions:
            reasons.append(f"持仓数量已达到上限 {settings.auto_trade_max_positions}")
        return reasons

    # ------------------------------------------------------------------
    # 子检查：当日累计买入金额
    # ------------------------------------------------------------------

    def _check_daily_buy_total(self, broker: TradingBroker, pending_amount: float) -> list[str]:
        reasons: list[str] = []
        used = self._query_daily_buy_total(broker)
        if used + pending_amount > settings.max_daily_buy_amount:
            reasons.append(
                f"当日累计买入 {used + pending_amount:.2f} 将超过上限 {settings.max_daily_buy_amount:.2f}"
                f"（已用 {used:.2f}）"
            )
        return reasons

    def _query_daily_buy_total(self, broker: TradingBroker | None) -> float:
        """查询当日已成交买入的实际支出金额。"""
        if broker is None:
            return 0.0
        try:
            today_str = date.today().isoformat()
            if hasattr(broker, "get_daily_buy_amount"):
                return float(broker.get_daily_buy_amount(today_str))
            if not hasattr(broker, "list_orders"):
                return 0.0
            orders = broker.list_orders(symbol=None, limit=500)
            total = 0.0
            for order in orders:
                if order.get("side") != "buy" or order.get("status") != "filled":
                    continue
                order_date = _extract_date_str(order.get("created_at") or order.get("executed_at", ""))
                if order_date == today_str:
                    total += float(order.get("amount") or 0)
            return round(total, 2)
        except Exception:
            return 0.0

    # ------------------------------------------------------------------
    # 子检查：交易时段
    # ------------------------------------------------------------------

    def _check_trading_hours(self) -> list[str]:
        """检查当前是否在交易时段内。

        paper 模式下仅返回警告，不阻断交易。
        live 模式下未来可改为强制阻断。
        """
        warnings: list[str] = []
        try:
            import zoneinfo

            tz = zoneinfo.ZoneInfo(settings.timezone)
            now = datetime.now(tz)
            weekday = now.weekday()
            if weekday >= 5:
                warnings.append("当前为非交易日（周末）")
                return warnings
            current_time = now.strftime("%H:%M")
            if current_time < "09:30" or current_time > "15:00":
                warnings.append(f"当前时间 {current_time} 不在交易时段 09:30-15:00")
            elif "11:30" < current_time < "13:00":
                warnings.append(f"当前时间 {current_time} 处于午休时段 11:30-13:00")
        except Exception:
            pass
        return warnings

    # ------------------------------------------------------------------
    # 当前限额配置快照
    # ------------------------------------------------------------------

    def _current_limits(self) -> dict[str, Any]:
        return {
            "trade_mode": settings.trade_mode,
            "allow_live_trading": settings.allow_live_trading,
            "allow_manual_open": settings.allow_manual_open,
            "allow_manual_close": settings.allow_manual_close,
            "allow_auto_buy": settings.allow_auto_buy,
            "allow_auto_close": settings.allow_auto_close,
            "max_order_amount": settings.max_order_amount,
            "max_daily_buy_amount": settings.max_daily_buy_amount,
            "max_position_amount": settings.max_position_amount,
            "max_positions": settings.auto_trade_max_positions,
        }


# ------------------------------------------------------------------
# 工具函数
# ------------------------------------------------------------------


def _normalize_symbol(symbol: str) -> str:
    return symbol.strip().upper().split(".")[0]


def _extract_date_str(datetime_str: str) -> str:
    """从 ISO 格式的时间字符串中提取日期部分 YYYY-MM-DD。"""
    if not datetime_str:
        return ""
    return datetime_str[:10]
