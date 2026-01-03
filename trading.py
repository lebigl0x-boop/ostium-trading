from __future__ import annotations

import logging
from typing import Sequence

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)


def compute_drawdown(
    entry_price: float, current_price: float, is_long: bool, leverage: float
) -> float:
    """
    Retourne le drawdown en % (0 si PnL >= 0). Inclut la leverage.
    """
    if entry_price <= 0 or leverage <= 0:
        return 0.0
    price_move_pct = ((current_price - entry_price) / entry_price) * 100
    pnl_pct = price_move_pct * (1 if is_long else -1) * leverage
    # Drawdown = perte non réalisée (positive) si PnL est négatif
    return max(0.0, -pnl_pct)


def compute_tp_sl_prices(
    entry_price: float,
    leverage: float,
    tp_pnl_targets: Sequence[float],
    sl_pnl: float | None,
    is_long: bool,
) -> tuple[list[float], float | None]:
    """
    Calcule les prix TP/SL à partir des cibles de PnL (% sur marge).
    Variation de prix: target(%) / leverage, appliquée au prix d'entrée.
    Exemple: target +50% avec levier 2 => mouvement de prix +25% sur le sous-jacent.
    """
    if entry_price <= 0 or leverage <= 0:
        return [], None

    tp_prices: list[float] = []
    for target in tp_pnl_targets:
        move = (target / 100) / leverage
        price = entry_price * (1 + move if is_long else 1 - move)
        tp_prices.append(price)

    sl_price = None
    if sl_pnl is not None:
        move = (sl_pnl / 100) / leverage
        sl_price = entry_price * (1 + move if is_long else 1 - move)

    return tp_prices, sl_price


class TradingClient:
    """
    Enveloppe Ostium SDK. En mode test, retourne une réponse simulée.
    """

    def __init__(
        self,
        rpc_url: str,
        vault_address: str,
        router_address: str,
        usdc_address: str,
        wallet_address: str,
        private_key: str,
        test_mode: bool = True,
    ) -> None:
        self.rpc_url = rpc_url
        self.vault_address = vault_address
        self.router_address = router_address
        self.usdc_address = usdc_address
        self.wallet_address = wallet_address
        self.private_key = private_key
        self.test_mode = test_mode

        self._client = None
        if not self.test_mode:
            try:
                try:
                    from ostium_python_sdk import OstiumSDK  # type: ignore
                except Exception as exc:  # noqa: BLE001
                    raise ImportError(f"OstiumSDK introuvable: {exc}") from exc

                # Le SDK attend un identifiant de réseau ("mainnet"/"testnet") et un RPC.
                self._client = OstiumSDK(
                    network="mainnet",
                    private_key=self.private_key,
                    rpc_url=self.rpc_url,
                )
            except Exception as exc:  # noqa: BLE001
                logger.error("Impossible d'initialiser l'Ostium SDK: %s", exc)
                self._client = None

    async def ensure_usdc_approval(self) -> None:
        if self.test_mode:
            logger.info("[TEST_MODE] Approve USDC ignoré.")
            return
        if not self._client:
            raise RuntimeError("Ostium SDK non initialisé")

        try:
            await self._client.approve_token(self.usdc_address)
            logger.info("Approval USDC OK.")
        except Exception as exc:  # noqa: BLE001
            logger.error("Erreur approval USDC: %s", exc)
            raise

    async def open_market_trade(
        self,
        pair_index: int,
        is_long: bool,
        amount_in: float,
        leverage: float,
        slippage_bps: int,
        tp_prices: Sequence[float] | None = None,
        sl_price: float | None = None,
    ) -> dict:
        params = {
            "pair_index": pair_index,
            "is_long": is_long,
            "amount_in": amount_in,
            "leverage": leverage,
            "slippage_bps": slippage_bps,
            "tp_prices": list(tp_prices or []),
            "sl_price": sl_price,
        }

        if self.test_mode or not self._client:
            logger.info("[TEST_MODE] Simulation de trade: %s", params)
            return {"status": "simulated", "params": params}

        try:
            receipt = await self._client.open_market_position(
                pair_index=pair_index,
                is_long=is_long,
                amount_in=amount_in,
                leverage=leverage,
                slippage_bps=slippage_bps,
                tp_prices=tp_prices or [],
                sl_price=sl_price,
            )
            logger.info("Trade soumis: %s", receipt)
            return {"status": "submitted", "tx": receipt}
        except Exception as exc:  # noqa: BLE001
            logger.error("Echec trade: %s", exc)
            raise

    @retry(
        retry=retry_if_exception_type(Exception),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=8),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def get_price(self, symbol: str) -> float:
        """
        Récupère le prix spot via le SDK (price.get_price). En TEST_MODE, renvoie 0.
        """
        if self.test_mode or not self._client:
            logger.info("[TEST_MODE] Prix simulé pour %s (0)", symbol)
            return 0.0

        try:
            if "-" in symbol:
                base, quote = symbol.split("-", 1)
            elif "/" in symbol:
                base, quote = symbol.split("/", 1)
            else:
                base, quote = symbol, "USD"

            price_data = await self._client.price.get_price(base, quote)  # type: ignore[attr-defined]

            if isinstance(price_data, (tuple, list)) and price_data:
                return float(price_data[0])
            if isinstance(price_data, (int, float)):
                return float(price_data)
            if isinstance(price_data, dict):
                for key in ("mid", "price", "value", "amount"):
                    if key in price_data:
                        return float(price_data[key])
            raise ValueError(f"Format de prix inattendu: {price_data}")
        except Exception as exc:  # noqa: BLE001
            logger.error("Echec get_price pour %s: %s", symbol, exc)
            raise

