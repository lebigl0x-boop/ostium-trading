from __future__ import annotations

import logging
from typing import Awaitable, Callable, Sequence

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

logger = logging.getLogger(__name__)


PositionsProvider = Callable[[], Awaitable[Sequence[dict]]]
TradeExecutor = Callable[[dict], Awaitable[dict]]


class TelegramBot:
    """
    Bot Telegram minimal avec /start, /positions et boutons de copie.
    """

    def __init__(
        self,
        token: str,
        allowed_chat_id: str,
        positions_provider: PositionsProvider,
        trade_executor: TradeExecutor,
    ) -> None:
        self.token = token
        self.allowed_chat_id = str(allowed_chat_id)
        self.positions_provider = positions_provider
        self.trade_executor = trade_executor
        self.app = Application.builder().token(self.token).build()

        self.app.add_handler(CommandHandler("start", self.start_command))
        self.app.add_handler(CommandHandler("positions", self.positions_command))
        self.app.add_handler(CommandHandler("wallet", self.positions_command))
        self.app.add_handler(CallbackQueryHandler(self.copy_trade_callback, pattern=r"^copy:"))

    async def _ensure_auth(self, update: Update) -> bool:
        chat_id = str(update.effective_chat.id) if update.effective_chat else ""
        if chat_id != self.allowed_chat_id:
            logger.warning("Chat non autorisé: %s", chat_id)
            if update.message:
                await update.message.reply_text("Accès refusé.")
            return False
        return True

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._ensure_auth(update):
            return
        await update.message.reply_text(
            "Bot Ostium prêt.\nCommandes:\n/positions - positions ouvertes\n/wallet - alias"
        )

    async def positions_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._ensure_auth(update):
            return

        positions = await self.positions_provider()
        if not positions:
            await update.message.reply_text("Aucune position détectée.")
            return

        for pos in positions:
            text = (
                f"Trader: {pos.get('trader')}\n"
                f"Pair: {pos.get('pair')}\n"
                f"Side: {'LONG' if pos.get('is_long') else 'SHORT'}\n"
                f"Drawdown: {pos.get('drawdown')}%\n"
                f"Size: {pos.get('size_usd')} USD\n"
            )
            keyboard = [
                [
                    InlineKeyboardButton(
                        "Copy LONG", callback_data=f"copy:{pos.get('pair_index')}:long"
                    ),
                    InlineKeyboardButton(
                        "Copy SHORT", callback_data=f"copy:{pos.get('pair_index')}:short"
                    ),
                ]
            ]
            await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

    async def copy_trade_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query = update.callback_query
        if not query:
            return

        if not await self._ensure_auth(update):
            await query.answer(text="Non autorisé", show_alert=True)
            return

        await query.answer()
        _, pair_index, side = query.data.split(":")
        is_long = side == "long"
        try:
            result = await self.trade_executor({"pair_index": int(pair_index), "is_long": is_long})
            await query.edit_message_text(f"Trade lancé: {result}")
        except Exception as exc:  # noqa: BLE001
            logger.error("Echec copy trade: %s", exc)
            await query.edit_message_text(f"Echec trade: {exc}")

    async def run(self) -> None:
        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling()

    async def stop(self) -> None:
        await self.app.updater.stop()
        await self.app.stop()
        await self.app.shutdown()

    async def send_text(self, text: str) -> None:
        try:
            await self.app.bot.send_message(chat_id=self.allowed_chat_id, text=text)
        except Exception as exc:  # noqa: BLE001
            logger.error("Impossible d'envoyer le message Telegram: %s", exc)

