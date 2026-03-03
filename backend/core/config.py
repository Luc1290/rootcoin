from pydantic import SecretStr
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    binance_api_key: SecretStr = SecretStr("")
    binance_secret_key: SecretStr = SecretStr("")

    stablecoins: str = "USDT,USDC,BUSD,DAI,TUSD,FDUSD"
    ignored_assets: str = "BNB"
    default_watchlist: str = "BTCUSDC,ETHUSDC,BNBUSDC"

    balance_snapshot_interval: int = 300
    price_record_interval: int = 60
    port: int = 8001
    price_retention_days: int = 30

    database_path: str = ""

    log_level: str = "INFO"
    log_format: str = "json"

    telegram_bot_token: SecretStr = SecretStr("")
    telegram_chat_id: SecretStr = SecretStr("")

    # Market analysis
    analysis_refresh_interval: int = 60
    macro_refresh_interval: int = 300
    whale_min_quote_qty: int = 100000
    heatmap_top_n: int = 50
    heatmap_refresh_interval: int = 300
    news_refresh_interval: int = 600
    news_max_items: int = 30
    orderbook_poll_interval: int = 10
    orderbook_depth_limit: int = 50
    orderbook_wall_threshold: float = 0.15

    # Opportunity detector
    opportunity_min_score: int = 35
    opportunity_cooldown_minutes: int = 30
    opportunity_max_items: int = 20

    @property
    def stablecoins_set(self) -> set[str]:
        return {s.strip() for s in self.stablecoins.split(",")}

    @property
    def ignored_assets_set(self) -> set[str]:
        return {s.strip() for s in self.ignored_assets.split(",") if s.strip()}

    @property
    def watchlist(self) -> list[str]:
        return [s.strip() for s in self.default_watchlist.split(",")]


settings = Settings()
