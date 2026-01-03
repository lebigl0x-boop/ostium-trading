from __future__ import annotations

import logging
from typing import Iterable, List, Sequence

import aiohttp
from gql import Client, gql
from gql.transport.aiohttp import AIOHTTPTransport
from pydantic import BaseModel

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
    transport = AIOHTTPTransport(url=subgraph_url, client_session=session)
    async with Client(transport=transport, fetch_schema_from_transport=False) as client:
        return await client.execute(gql(query), variable_values=variables)


async def get_pairs(
    session: aiohttp.ClientSession, subgraph_url: str
) -> list[Pair]:
    query = """
    query Pairs {
      pairs {
        id
        pairIndex
        symbol
      }
    }
    """
    data = await _execute_query(session, subgraph_url, query)
    pairs_raw = data.get("pairs", []) if data else []
    pairs: list[Pair] = []
    for item in pairs_raw:
        try:
            pairs.append(
                Pair(
                    id=item["id"],
                    pair_index=int(item.get("pairIndex", 0)),
                    symbol=item.get("symbol", f"PAIR-{item.get('pairIndex', '?')}"),
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
    query Positions($accounts: [Bytes!]!) {
      positions(where: { account_in: $accounts, sizeUsd_gt: 0 }) {
        id
        account
        pairIndex
        isLong
        sizeUsd
        collateralUsd
        entryPrice
        leverage
      }
    }
    """
    data = await _execute_query(
        session, subgraph_url, query, {"accounts": [a.lower() for a in trader_addresses]}
    )
    raw_positions = data.get("positions", []) if data else []
    positions: list[Position] = []
    for item in raw_positions:
        try:
            positions.append(
                Position(
                    id=item["id"],
                    trader=item["account"],
                    pair_index=int(item.get("pairIndex", 0)),
                    is_long=bool(item.get("isLong", True)),
                    size_usd=float(item.get("sizeUsd", 0)),
                    collateral_usd=float(item.get("collateralUsd", 0)),
                    entry_price=float(item.get("entryPrice", 0)),
                    leverage=float(item.get("leverage", 1)),
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Position invalide ignorée: %s (%s)", item, exc)
    return positions


