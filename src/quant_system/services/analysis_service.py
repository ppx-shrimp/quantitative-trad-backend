from quant_system.data.market_data import MarketDataProvider
from quant_system.services.feature_service import FeatureService


class AnalysisService:
    def __init__(self) -> None:
        self.market_data = MarketDataProvider()
        self.feature_service = FeatureService()

    def analyze(self, symbol: str) -> dict:
        snapshot = self.market_data.get_snapshot(symbol)
        feature_analysis = self.feature_service.analyze_symbol(symbol, period="daily")
        return {
            "symbol": snapshot.symbol,
            "price": snapshot.price,
            "change_pct": snapshot.change_pct,
            "volume": snapshot.volume,
            "summary": feature_analysis["summary"],
            "scores": feature_analysis["scores"],
            "feature_status": feature_analysis["status"],
            "latest_feature": feature_analysis.get("latest_feature"),
        }
