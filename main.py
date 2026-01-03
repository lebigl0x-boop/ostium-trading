from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp
import ssl
import certifi

from alerts import TelegramBot
from config import BotConfig, EnvSettings, load_bot_config, load_env_settings
from logging_config import setup_logging
from subgraph import get_pairs, get_positions
from trading import TradingClient, compute_drawdown, compute_tp_sl_prices


async def build_positions_snapshot(
    session: aiohttp.ClientSession,
    env: EnvSettings,
    cfg: BotConfig,
    pair_symbols: dict[int, str],
    trading_client: TradingClient,
) -> list[dict[str, Any]]:
    traders: list[str] = cfg.traders or ([env.target_wallet] if env.target_wallet else [])
    if not traders:
        return []
    positions = await get_positions(session, env.ostium_subgraph_url, traders)
    snapshot: list[dict[str, Any]] = []
    for pos in positions:
        symbol = pair_symbols.get(pos.pair_index, f"PAIR-{pos.pair_index}")
        # Prix spot avec retry; fallback sur entry_price en cas d'échec pour éviter le crash.
        try:
            current_price = await trading_client.get_price(symbol)
            if current_price <= 0:
                current_price = pos.entry_price
        except Exception:
            current_price = pos.entry_price

        pnl_pct = ((current_price - pos.entry_price) / pos.entry_price) * (
            1 if pos.is_long else -1
        ) * pos.leverage * 100
        drawdown = compute_drawdown(pos.entry_price, current_price, pos.is_long, pos.leverage)
        snapshot.append(
            {
                "id": pos.id,
                "trader": pos.trader,
                "pair_index": pos.pair_index,
                "pair": symbol,
                "is_long": pos.is_long,
                "drawdown": round(drawdown, 2),
                "pnl_pct": round(pnl_pct, 2),
                "size_usd": pos.size_usd,
                "entry_price": pos.entry_price,
                "current_price": current_price,
                "leverage": pos.leverage,
            }
        )
    return snapshot


async def monitor_drawdown(
    bot: TelegramBot,
    session: aiohttp.ClientSession,
    env: EnvSettings,
    cfg: BotConfig,
    pair_symbols: dict[int, str],
    trading_client: TradingClient,
) -> None:
    while True:
        try:
            positions = await build_positions_snapshot(
                session, env, cfg, pair_symbols, trading_client
            )
            for pos in positions:
                dd = pos["drawdown"]
                if cfg.drawdown_min <= dd <= cfg.drawdown_max:
                    await bot.send_text(
                        f"Drawdown {dd}% sur {pos['pair']} (trader {pos['trader']}, "
                        f"{'LONG' if pos['is_long'] else 'SHORT'}) | "
                        f"Entry {pos['entry_price']}, Prix {pos['current_price']}"
                    )
        except Exception as exc:  # noqa: BLE001
            logging.exception("Erreur monitor_drawdown: %s", exc)
        await asyncio.sleep(env.poll_interval_seconds)


async def main() -> None:
    env = load_env_settings()
    cfg = load_bot_config()

    setup_logging(env.log_level)
    logging.info("Configuration chargée. TEST_MODE=%s", env.test_mode)

    trading_client = TradingClient(
        rpc_url=env.arbitrum_rpc_url,
        vault_address=env.ostium_vault_address,
        router_address=env.ostium_router_address,
        usdc_address=env.usdc_address,
        wallet_address=env.wallet_address,
        private_key=env.private_key,
        test_mode=env.test_mode,
    )

    ssl_context = ssl.create_default_context(cafile=certifi.where())
    connector = aiohttp.TCPConnector(ssl=ssl_context)

    async with aiohttp.ClientSession(connector=connector) as session:
        pairs = await get_pairs(session, env.ostium_subgraph_url)
        pair_symbols = {p.pair_index: p.symbol for p in pairs}

        async def positions_provider() -> list[dict[str, Any]]:
            return await build_positions_snapshot(session, env, cfg, pair_symbols, trading_client)

        async def trade_executor(payload: dict) -> dict:
            pair_index = int(payload["pair_index"])
            is_long = bool(payload["is_long"])
            tp_prices, sl_price = compute_tp_sl_prices(
                entry_price=payload.get("entry_price", 0) or 0,
                leverage=cfg.leverage,
                tp_pnl_targets=cfg.tp_pnl_targets,
                sl_pnl=cfg.sl_pnl,
                is_long=is_long,
            )
            # Si aucun entry_price fourni, on omet TP/SL pour éviter un mauvais calcul.
            if payload.get("entry_price") is None:
                tp_prices = []
                sl_price = None
            await trading_client.ensure_usdc_approval()
            return await trading_client.open_market_trade(
                pair_index=pair_index,
                is_long=is_long,
                amount_in=cfg.amount_in,
                leverage=cfg.leverage,
                slippage_bps=cfg.slippage_bps,
                tp_prices=tp_prices if cfg.copy_tp_sl else [],
                sl_price=sl_price if cfg.copy_tp_sl else None,
            )

        bot = TelegramBot(
            token=env.telegram_bot_token,
            allowed_chat_id=env.telegram_chat_id,
            positions_provider=positions_provider,
            trade_executor=trade_executor,
        )

        monitor_task = asyncio.create_task(
            monitor_drawdown(bot, session, env, cfg, pair_symbols, trading_client)
        )
        bot_task = asyncio.create_task(bot.run())

        try:
            await asyncio.gather(monitor_task, bot_task)
        except asyncio.CancelledError:
            logging.info("Arrêt demandé.")
        finally:
            await bot.stop()


if __name__ == "__main__":
    asyncio.run(main())

