import json
import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class EnvSettings(BaseModel):
    arbitrum_rpc_url: str
    ostium_subgraph_url: str
    ostium_vault_address: str
    ostium_router_address: str
    usdc_address: str
    target_wallet: str | None = None
    drawdown_threshold_min: float = 20.0
    drawdown_threshold_max: float = 30.0
    poll_interval_seconds: int = 30
    price_precision: int = 10**30
    usd_precision: int = 10**30
    usdc_decimals: int = 1_000_000
    telegram_bot_token: str
    telegram_chat_id: str
    arbiscan_api_key: str | None = None
    private_key: str
    wallet_address: str
    test_mode: bool = True
    log_level: str = "INFO"

    class Config:
        extra = "ignore"


class BotConfig(BaseModel):
    drawdown_min: float = Field(..., gt=0)
    drawdown_max: float = Field(..., gt=0)
    mode: Literal["position", "paire"] = "position"
    amount_in: float = Field(..., gt=0)
    leverage: float = Field(..., gt=1)
    tp_pnl_targets: list[float] = Field(default_factory=lambda: [5.0, 10.0])
    sl_pnl: float = -10.0
    slippage_bps: int = 50
    traders: list[str] = Field(default_factory=list)
    copy_tp_sl: bool = True

    class Config:
        extra = "ignore"


def load_env_settings(env_path: str | Path = ".env") -> EnvSettings:
    load_dotenv(env_path)
    data = {
        "arbitrum_rpc_url": os.getenv("ARBITRUM_RPC_URL"),
        "ostium_subgraph_url": os.getenv("OSTIUM_SUBGRAPH_URL"),
        "ostium_vault_address": os.getenv("OSTIUM_VAULT_ADDRESS"),
        "ostium_router_address": os.getenv("OSTIUM_ROUTER_ADDRESS"),
        "usdc_address": os.getenv("USDC_ADDRESS"),
        "target_wallet": os.getenv("TARGET_WALLET"),
        "drawdown_threshold_min": float(os.getenv("DRAWDOWN_THRESHOLD_MIN", 20.0)),
        "drawdown_threshold_max": float(os.getenv("DRAWDOWN_THRESHOLD_MAX", 30.0)),
        "poll_interval_seconds": int(os.getenv("POLL_INTERVAL_SECONDS", 30)),
        "price_precision": int(os.getenv("PRICE_PRECISION", 10**30)),
        "usd_precision": int(os.getenv("USD_PRECISION", 10**30)),
        "usdc_decimals": int(os.getenv("USDC_DECIMALS", 1_000_000)),
        "telegram_bot_token": os.getenv("TELEGRAM_BOT_TOKEN", ""),
        "telegram_chat_id": os.getenv("TELEGRAM_CHAT_ID", ""),
        "arbiscan_api_key": os.getenv("ARBISCAN_API_KEY"),
        "private_key": os.getenv("PRIVATE_KEY", ""),
        "wallet_address": os.getenv("WALLET_ADDRESS", ""),
        "test_mode": _parse_bool(os.getenv("TEST_MODE"), default=True),
        "log_level": os.getenv("LOG_LEVEL", "INFO"),
    }
    try:
        return EnvSettings(**data)
    except ValidationError as exc:
        raise ValueError(f"Erreur de configuration .env: {exc}") from exc


def load_bot_config(config_path: str | Path = "config.json") -> BotConfig:
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(
            f"config.json manquant à {path}. Copiez config.json ou config.example."
        )
    raw = path.read_text()
    try:
        data = json.loads(raw)
        return BotConfig(**data)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ValueError(f"config.json invalide: {exc}") from exc


def save_bot_config(config: BotConfig, config_path: str | Path = "config.json") -> None:
    path = Path(config_path)
    path.write_text(config.model_dump_json(indent=2))


