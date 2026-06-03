from quant_system.services.prediction_service import PredictionService
from quant_system.strategies.base import Strategy


class OpeningPredictionStrategy(Strategy):
    name = "opening_prediction"

    def __init__(self) -> None:
        self.prediction_service = PredictionService()

    def should_open(self, symbol: str) -> tuple[bool, str]:
        prediction = self.prediction_service.predict_kline(symbol)
        allowed = prediction["direction"] == "up" and prediction["confidence"] >= 0.55
        return allowed, prediction["reason"]

    def should_close(self, symbol: str) -> tuple[bool, str]:
        prediction = self.prediction_service.predict_kline(symbol)
        should_close = prediction["direction"] in {"down", "flat"} or prediction["confidence"] < 0.50
        return should_close, prediction["reason"]
