from __future__ import annotations

import asyncio
import logging
from typing import Sequence

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential
from web3 import Web3

logger = logging.getLogger(__name__)


ERC20_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function",
    }
]


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
        network: str = "mainnet",
    ) -> None:
        self.rpc_url = rpc_url
        self.vault_address = vault_address
        self.router_address = router_address
        self.usdc_address = usdc_address
        self.wallet_address = wallet_address
        self.private_key = private_key
        self.test_mode = test_mode
        self.network = network

        self._client = None
        if not self.test_mode:
            try:
                from ostium_python_sdk import OstiumSDK  # type: ignore

                self._client = OstiumSDK(
                    network=self.network,
                    private_key=self.private_key,
                    rpc_url=self.rpc_url,
                    verbose=True,
                )
            except Exception as exc:  # noqa: BLE001
                logger.error("Impossible d'initialiser l'Ostium SDK: %s", exc)
                self._client = None

    @staticmethod
    def _from_wei(value: float | int | str, decimals: int) -> float:
        try:
            return float(value) / (10**decimals)
        except Exception:
            return 0.0

    @staticmethod
    def _price_precision(base: str, quote: str) -> int:
        base_up = (base or "").upper()
        quote_up = (quote or "").upper()
        if quote_up == "USD":
            if base_up in {"BTC", "ETH"}:
                return 2
            if base_up in {"XAU", "XAG"}:
                return 2
            # défaut forex/indices
            return 4
        # crypto générique
        return 8

    async def fetch_pairs(self) -> list[dict]:
        if self.test_mode or not self._client:
            return []
        try:
            pairs = await self._client.subgraph.get_pairs()  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            logger.error("Echec fetch pairs: %s", exc)
            raise
        result: list[dict] = []
        for p in pairs:
            base = p.get("from", "UNKNOWN")
            quote = p.get("to", "USD")
            pair_id = int(p.get("id", 0))
            result.append(
                {
                    "id": pair_id,
                    "base": base,
                    "quote": quote,
                    "symbol": f"{base}-{quote}",
                }
            )
        return result

    async def fetch_open_trades(self, trader: str) -> list[dict]:
        if not trader:
            return []
        if self.test_mode or not self._client:
            return []
        try:
            trades = await self._client.subgraph.get_open_trades(trader.lower())  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            logger.error("Echec fetch open trades: %s", exc)
            raise
        normalized: list[dict] = []
        for t in trades:
            try:
                pair = t.get("pair") or {}
                pair_id = int(pair.get("id", 0))
                base = pair.get("from", "UNKNOWN")
                quote = pair.get("to", "USD")
                open_price = self._from_wei(t.get("openPrice", 0), 18)
                leverage = self._from_wei(t.get("leverage", 0), 2)
                collateral = self._from_wei(t.get("collateral", 0), 6)
                notional = self._from_wei(t.get("tradeNotional", 0) or t.get("notional", 0), 18)
                normalized.append(
                    {
                        "id": t.get("tradeID") or t.get("id"),
                        "trader": t.get("trader"),
                        "pair_index": pair_id,
                        "base": base,
                        "quote": quote,
                        "is_long": bool(t.get("isBuy", True)),
                        "size_usd": notional or collateral * leverage,
                        "collateral_usd": collateral,
                        "entry_price": open_price,
                        "leverage": leverage,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Trade invalide ignoré: %s (%s)", t, exc)
        return normalized

    async def ensure_usdc_approval(self) -> None:
        # Le SDK fait déjà l'approve dans perform_trade; on laisse vide pour compatibilité.
        if self.test_mode:
            return
        if not self._client:
            raise RuntimeError("Ostium SDK non initialisé")

    @retry(
        retry=retry_if_exception_type(Exception),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=8),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def get_price(self, base: str, quote: str) -> float:
        """
        Récupère le prix spot via le SDK (price.get_price). En TEST_MODE, renvoie 0.
        """
        if self.test_mode or not self._client:
            logger.info("[TEST_MODE] Prix simulé pour %s-%s (0)", base, quote)
            return 0.0

        try:
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
            logger.error("Echec get_price pour %s-%s: %s", base, quote, exc)
            raise

    async def get_usdc_balance(self) -> float:
        """
        Lecture du solde USDC du wallet via web3 (synchrone, déporté en thread).
        """
        if not self.wallet_address or not self.usdc_address:
            return 0.0
        if self.test_mode:
            logger.info("[TEST_MODE] Solde USDC simulé (0)")
            return 0.0

        def _read() -> float:
            try:
                w3 = Web3(Web3.HTTPProvider(self.rpc_url))
                contract = w3.eth.contract(
                    address=Web3.to_checksum_address(self.usdc_address), abi=ERC20_ABI
                )
                balance_wei = contract.functions.balanceOf(
                    Web3.to_checksum_address(self.wallet_address)
                ).call()
                return float(balance_wei) / 1_000_000
            except Exception as exc:  # noqa: BLE001
                logger.error("Erreur lecture solde USDC: %s", exc)
                return 0.0

        return await asyncio.to_thread(_read)

    async def open_copy_trade(
        self,
        pair_index: int,
        base: str,
        quote: str,
        is_long: bool,
        amount_in: float,
        leverage: float,
        slippage_bps: int,
        tp_prices: Sequence[float],
        sl_price: float | None,
    ) -> dict:
        """
        Ouvre un trade unique (full amount) en market, sans TP/SL dans perform_trade.
        Les TP/SL pourront être posés ensuite via update_tp/update_sl si besoin.
        Le SDK attend les montants en USDC "entier" (pas les 6 décimales), il applique son scaling.
        """
        # Prix actuel via SDK price.get_price
        price_data = await self._client.price.get_price(base, quote) if self._client else None  # type: ignore[attr-defined]
        if price_data is None:
            raise ValueError("Prix indisponible pour le copy-trade.")
        if isinstance(price_data, (list, tuple)):
            current_price = float(price_data[0])
        else:
            current_price = float(price_data)
        if current_price <= 0:
            raise ValueError("Prix actuel indisponible pour le copy-trade.")

        params = {
            "collateral": int(round(amount_in)),  # USDC brut, le SDK scale en interne
            "leverage": int(round(leverage)),
            "asset_type": int(pair_index),
            "direction": bool(is_long),
            "order_type": "MARKET",
        }

        if self.test_mode or not self._client:
            return {
                "status": "simulated",
                "current_price": current_price,
                "trades": [params],
            }

        try:
            receipt = self._client.ostium.perform_trade(params, at_price=current_price)  # type: ignore[attr-defined]
            return {
                "status": "submitted",
                "current_price": current_price,
                "receipts": [receipt],
                "params": params,
            }
        except Exception as exc:  # noqa: BLE001
            logger.error("Echec copy-trade: %s", exc)
            raise

