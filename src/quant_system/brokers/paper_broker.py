from quant_system.brokers.execution import build_trade_execution
from quant_system.domain.models import Position


class PaperBroker:
    """模拟券商。

    只记录内存持仓，不连接真实交易账户。后续接真实券商时，应新增 Broker 接口实现，不直接修改策略层。
    """

    def __init__(self, initial_cash: float) -> None:
        self.cash = initial_cash
        self.positions: dict[str, Position] = {}

    def buy(self, symbol: str, quantity: int, price: float, decision: dict | None = None) -> dict:
        execution = build_trade_execution("buy", quantity, price)
        cost = execution.amount
        if cost > self.cash:
            return {"accepted": False, "side": "buy", "symbol": symbol, "quantity": quantity, "price": execution.price, "amount": cost, "execution": execution.to_dict(), "reason": "模拟账户现金不足", "cash": round(self.cash, 2)}
        self.cash = round(self.cash - cost, 2)
        current = self.positions.get(symbol)
        if current:
            total_quantity = current.quantity + quantity
            current.avg_price = ((current.avg_price * current.quantity) + cost) / total_quantity
            current.quantity = total_quantity
        else:
            self.positions[symbol] = Position(symbol=symbol, quantity=quantity, avg_price=cost / quantity)
        return {"accepted": True, "side": "buy", "symbol": symbol, "quantity": quantity, "price": execution.price, "amount": cost, "cash": self.cash, "execution": execution.to_dict()}

    def sell(self, symbol: str, quantity: int | None, price: float, decision: dict | None = None) -> dict:
        current = self.positions.get(symbol)
        if not current:
            return {"accepted": False, "side": "sell", "symbol": symbol, "quantity": quantity or 0, "price": price, "reason": "没有可平仓持仓", "cash": round(self.cash, 2)}
        sell_quantity = quantity or current.quantity
        if sell_quantity > current.quantity:
            return {"accepted": False, "side": "sell", "symbol": symbol, "quantity": sell_quantity, "price": price, "reason": "平仓数量超过持仓数量", "cash": round(self.cash, 2)}
        execution = build_trade_execution("sell", sell_quantity, price)
        realized_pnl = execution.amount - (current.avg_price * sell_quantity)
        self.cash = round(self.cash + execution.amount, 2)
        current.quantity -= sell_quantity
        if current.quantity == 0:
            del self.positions[symbol]
        return {"accepted": True, "side": "sell", "symbol": symbol, "quantity": sell_quantity, "price": execution.price, "amount": execution.amount, "cash": self.cash, "realized_pnl": round(realized_pnl, 2), "execution": execution.to_dict()}

    def get_position(self, symbol: str) -> Position | None:
        return self.positions.get(symbol.upper())

    def has_position(self, symbol: str) -> bool:
        return self.get_position(symbol) is not None

    def position_count(self) -> int:
        return len(self.positions)

    def list_positions(self) -> list[dict]:
        return [
            {
                "symbol": position.symbol,
                "quantity": position.quantity,
                "avg_price": round(position.avg_price, 2),
                "opened_at": position.opened_at.isoformat(),
            }
            for position in self.positions.values()
        ]
