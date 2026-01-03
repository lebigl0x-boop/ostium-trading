from __future__ import annotations

import logging
from typing import Iterable, List, Sequence

import aiohttp
from pydantic import BaseModel

try:
    from ostium_python_sdk.config import NetworkConfig
except Exception:  # pragma: no cover
    NetworkConfig = None  # type: ignore

logger = logging.getLogger(__name__)


class Pair(BaseModel):
    id: str
    pair_index: int
    symbol: str


class Position(BaseModel):
    id: str
    trader: str
    pair_index: int
    is_long: bool
    size_usd: float
    collateral_usd: float
    entry_price: float
    leverage: float


async def _execute_query(
    session: aiohttp.ClientSession,
    subgraph_url: str,
    query: str,
    variables: dict | None = None,
) -> dict:
    """
    Exécute une requête GraphQL via aiohttp (sans gql.Client pour éviter l'API client_session).
    Tente en cascade sur subgraph_url puis sur une URL Goldsky de secours si disponible.
    """
    urls_to_try = [subgraph_url]
    if NetworkConfig:
        urls_to_try.append(NetworkConfig.mainnet().graph_url)
        urls_to_try.append(NetworkConfig.testnet().graph_url)

    last_error: Exception | None = None
    for url in urls_to_try:
        if not url:
            continue
        payload = {"query": query, "variables": variables or {}}
        try:
            async with session.post(url, json=payload, timeout=20) as resp:
                resp.raise_for_status()
                data = await resp.json()
                if not isinstance(data, dict):
                    raise ValueError(f"Réponse inattendue du subgraph: {data}")
                if "errors" in data:
                    # Si endpoint retiré, on passe à l'URL suivante.
                    msg = str(data["errors"])
                    if "removed" in msg or "endpoint" in msg:
                        raise RuntimeError(msg)
                    raise ValueError(f"Erreur subgraph: {data['errors']}")
                return data.get("data", {})
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logging.warning("Subgraph %s en échec: %s", url, exc)
            continue

    if last_error:
        raise last_error
    raise RuntimeError("Aucune URL subgraph valide trouvée")


async def get_pairs(
    session: aiohttp.ClientSession, subgraph_url: str
) -> list[Pair]:
    query = """
    query Pairs {
      pairs(first: 500) {
        id
        from
        to
      }
    }
    """
    data = await _execute_query(session, subgraph_url, query)
    pairs_raw = data.get("pairs", []) if data else []
    pairs: list[Pair] = []
    for item in pairs_raw:
        try:
            symbol = f"{item.get('from', 'UNKNOWN')}-{item.get('to', 'USD')}"
            pairs.append(
                Pair(
                    id=item["id"],
                    pair_index=int(item.get("id", 0)),
                    symbol=symbol,
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Pair invalide ignoré: %s (%s)", item, exc)
    return pairs


async def get_positions(
    session: aiohttp.ClientSession,
    subgraph_url: str,
    trader_addresses: Sequence[str],
) -> list[Position]:
    if not trader_addresses:
        return []

    query = """
    query Trades($accounts: [Bytes!]!) {
      trades(where: { isOpen: true, trader_in: $accounts }) {
        id
        tradeID
        trader
        isBuy
        notional
        tradeNotional
        collateral
        leverage
        openPrice
        pair { id from to }
      }
    }
    """
    data = await _execute_query(
        session, subgraph_url, query, {"accounts": [a.lower() for a in trader_addresses]}
    )
    raw_positions = data.get("trades", []) if data else []
    positions: list[Position] = []
    for item in raw_positions:
        try:
            pair = item.get("pair") or {}
            symbol = f"{pair.get('from', 'UNKNOWN')}-{pair.get('to', 'USD')}"
            notional = item.get("notional") or item.get("tradeNotional") or 0
            positions.append(
                Position(
                    id=item.get("tradeID") or item.get("id"),
                    trader=item.get("trader"),
                    pair_index=int(pair.get("id", 0)),
                    is_long=bool(item.get("isBuy", True)),
                    size_usd=float(notional),
                    collateral_usd=float(item.get("collateral") or 0),
                    entry_price=float(item.get("openPrice") or 0),
                    leverage=float(item.get("leverage") or 1),
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Position invalide ignorée: %s (%s)", item, exc)
    return positions


