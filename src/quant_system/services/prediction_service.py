from quant_system.data.market_data import MarketDataProvider
from quant_system.services.feature_service import FeatureService


class PredictionService:
    def __init__(self) -> None:
        self.market_data = MarketDataProvider()
        self.feature_service = FeatureService()

    def predict_kline(self, symbol: str) -> dict:
        return self.feature_service.predict_symbol(symbol, period="daily")
