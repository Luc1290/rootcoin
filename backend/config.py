from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    binance_api_key: str = ""
    binance_secret_key: str = ""

    stablecoins: str = "USDT,USDC,BUSD,DAI,TUSD,FDUSD"
    default_watchlist: str = "BTCUSDC,ETHUSDC,BNBUSDC"

    balance_snapshot_interval: int = 300
    price_record_interval: int = 60
    port: int = 8001
    price_retention_days: int = 30

    log_level: str = "INFO"
    log_format: str = "json"

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    @property
    def stablecoins_set(self) -> set[str]:
        return {s.strip() for s in self.stablecoins.split(",")}

    @property
    def watchlist(self) -> list[str]:
        return [s.strip() for s in self.default_watchlist.split(",")]


settings = Settings()
