from __future__ import annotations

from dataclasses import dataclass

from quant_system.core.config import settings


@dataclass(frozen=True)
class TradeExecution:
    """本地模拟成交结果。

    price 是实际成交价；amount 是本次交易对现金账户的净影响金额：
    买入为含费用成本，卖出为扣除费用后的回款。
    """

    side: str
    requested_price: float
    price: float
    quantity: int
    gross_amount: float
    commission: float
    stamp_duty: float
    transfer_fee: float
    total_fee: float
    amount: float
    cash_delta: float

    def to_dict(self) -> dict:
        return {
            "requested_price": self.requested_price,
            "price": self.price,
            "quantity": self.quantity,
            "gross_amount": self.gross_amount,
            "commission": self.commission,
            "stamp_duty": self.stamp_duty,
            "transfer_fee": self.transfer_fee,
            "total_fee": self.total_fee,
            "amount": self.amount,
            "cash_delta": self.cash_delta,
            "slippage_bps": settings.paper_slippage_bps,
        }


def build_trade_execution(side: str, quantity: int, price: float) -> TradeExecution:
    if quantity <= 0:
        raise ValueError("交易数量必须大于 0")
    if price <= 0:
        raise ValueError("交易价格必须大于 0")
    normalized_side = side.strip().lower()
    if normalized_side not in {"buy", "sell"}:
        raise ValueError(f"不支持的交易方向：{side}")

    slippage_factor = settings.paper_slippage_bps / 10000
    if normalized_side == "buy":
        execution_price = price * (1 + slippage_factor)
    else:
        execution_price = price * (1 - slippage_factor)
    execution_price = round(execution_price, 4)

    gross_amount = round(quantity * execution_price, 2)
    commission = _commission(gross_amount)
    stamp_duty = _stamp_duty(normalized_side, gross_amount)
    transfer_fee = _transfer_fee(gross_amount)
    total_fee = round(commission + stamp_duty + transfer_fee, 2)

    if normalized_side == "buy":
        amount = round(gross_amount + total_fee, 2)
        cash_delta = -amount
    else:
        amount = round(gross_amount - total_fee, 2)
        cash_delta = amount

    return TradeExecution(
        side=normalized_side,
        requested_price=round(price, 4),
        price=execution_price,
        quantity=quantity,
        gross_amount=gross_amount,
        commission=commission,
        stamp_duty=stamp_duty,
        transfer_fee=transfer_fee,
        total_fee=total_fee,
        amount=amount,
        cash_delta=cash_delta,
    )


def _commission(gross_amount: float) -> float:
    fee = gross_amount * settings.paper_commission_rate
    if fee > 0:
        fee = max(fee, settings.paper_min_commission)
    return round(fee, 2)


def _stamp_duty(side: str, gross_amount: float) -> float:
    if side != "sell":
        return 0.0
    return round(gross_amount * settings.paper_stamp_duty_rate, 2)


def _transfer_fee(gross_amount: float) -> float:
    return round(gross_amount * settings.paper_transfer_fee_rate, 2)
