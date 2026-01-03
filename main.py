from __future__ import annotations

import asyncio
import logging
from typing import Any

from alerts import TelegramBot
from config import BotConfig, EnvSettings, load_bot_config, load_env_settings
from logging_config import setup_logging
from trading import TradingClient, compute_drawdown, compute_tp_sl_prices


async def build_positions_snapshot(
    cfg: BotConfig, env: EnvSettings, pair_map: dict[int, dict], trading_client: TradingClient
) -> list[dict[str, Any]]:
    traders: list[str] = cfg.traders or ([env.target_wallet] if env.target_wallet else [])
    if not traders:
        return []

    positions: list[dict[str, Any]] = []
    for trader in traders:
        try:
            trades = await trading_client.fetch_open_trades(trader)
            positions.extend(trades)
        except Exception:
            continue

    price_cache: dict[int, float] = {}
    snapshot: list[dict[str, Any]] = []
    for pos in positions:
        pair_info = pair_map.get(pos["pair_index"], {})
        base = pair_info.get("base", "UNKNOWN")
        quote = pair_info.get("quote", "USD")
        symbol = pair_info.get("symbol", f"{base}-{quote}")

        if pos["pair_index"] in price_cache:
            current_price = price_cache[pos["pair_index"]]
        else:
            try:
                current_price = await trading_client.get_price(base, quote)
                if current_price <= 0:
                    current_price = pos["entry_price"]
            except Exception:
                current_price = pos["entry_price"]
            price_cache[pos["pair_index"]] = current_price

        pnl_pct = ((current_price - pos["entry_price"]) / pos["entry_price"]) * (
            1 if pos["is_long"] else -1
        ) * pos["leverage"] * 100
        drawdown = compute_drawdown(
            pos["entry_price"], current_price, pos["is_long"], pos["leverage"]
        )
        snapshot.append(
            {
                "id": pos.get("id"),
                "trader": pos.get("trader"),
                "pair_index": pos["pair_index"],
                "pair": symbol,
                "is_long": pos["is_long"],
                "drawdown": round(drawdown, 2),
                "pnl_pct": round(pnl_pct, 2),
                "size_usd": pos.get("size_usd"),
                "entry_price": pos["entry_price"],
                "current_price": current_price,
                "leverage": pos["leverage"],
                "base": base,
                "quote": quote,
            }
        )
    logging.info("Positions trouvées: %s", len(snapshot))
    for pos in snapshot:
        logging.info(
            "Pos %s | pair=%s | side=%s | entry=%.6f | px=%.6f | lev=%.2f | pnl=%.2f%% | dd=%.2f%%",
            pos.get("id"),
            pos.get("pair"),
            "LONG" if pos.get("is_long") else "SHORT",
            pos.get("entry_price"),
            pos.get("current_price"),
            pos.get("leverage"),
            pos.get("pnl_pct"),
            pos.get("drawdown"),
        )
    return snapshot


async def monitor_drawdown(
    bot: TelegramBot,
    env: EnvSettings,
    cfg: BotConfig,
    pair_map: dict[int, dict],
    trading_client: TradingClient,
) -> None:
    while True:
        try:
            positions = await build_positions_snapshot(cfg, env, pair_map, trading_client)
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
        network=env.ostium_network,
    )

    pairs = await trading_client.fetch_pairs()
    pair_map = {p["id"]: p for p in pairs}

    async def positions_provider() -> list[dict[str, Any]]:
        return await build_positions_snapshot(cfg, env, pair_map, trading_client)

    async def trade_executor(payload: dict) -> dict:
        pair_index = int(payload["pair_index"])
        pair = pair_map.get(pair_index, {"base": "UNKNOWN", "quote": "USD", "symbol": f"{pair_index}"})
        is_long = bool(payload["is_long"])
        current_price = await trading_client.get_price(pair["base"], pair["quote"])
        tp_prices, sl_price = compute_tp_sl_prices(
            entry_price=current_price,
            leverage=cfg.leverage,
            tp_pnl_targets=cfg.tp_pnl_targets,
            sl_pnl=cfg.sl_pnl,
            is_long=is_long,
        )
        if not cfg.copy_tp_sl:
            tp_prices = []
            sl_price = None
        await trading_client.ensure_usdc_approval()
        return await trading_client.open_copy_trade(
            pair_index=pair_index,
            base=pair["base"],
            quote=pair["quote"],
            is_long=is_long,
            amount_in=cfg.amount_in,
            leverage=cfg.leverage,
            slippage_bps=cfg.slippage_bps,
            tp_prices=tp_prices,
            sl_price=sl_price,
        )

    bot = TelegramBot(
        token=env.telegram_bot_token,
        allowed_chat_id=env.telegram_chat_id,
        positions_provider=positions_provider,
        trade_executor=trade_executor,
    )

    monitor_task = asyncio.create_task(
        monitor_drawdown(bot, env, cfg, pair_map, trading_client)
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

