"""
Steven — Live Trader
Steps: 8 (open position log), exit monitoring, close position
"""

import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from models import Market, Position

# Long-term Pinecone memory (non-fatal if unavailable)
try:
    from pinecone_memory import memory as _mem
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent.parent))
    try:
        from pinecone_memory import memory as _mem
    except Exception:
        _mem = None

log = logging.getLogger("steven")

POSITIONS_FILE = Path(__file__).parent.parent / "data" / "active_positions.json"

TIER_SIZES = {1: 25.0, 2: 50.0, 3: 100.0}


def _load_positions() -> list[dict]:
    if not POSITIONS_FILE.exists():
        return []
    with open(POSITIONS_FILE, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("positions", [])


def _save_positions(positions: list[dict]) -> None:
    with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
        json.dump({"positions": positions}, f, indent=2)


# ------------------------------------------------------------------ #
#  Step 8 — Open Position                                             #
# ------------------------------------------------------------------ #

def open_position(
    market: Market,
    direction: str,
    tier: int,
    sim_confidence: float,
) -> Position:
    """
    Step 8: Log an approved position to active_positions.json.
    Returns Position dataclass.
    """
    size = TIER_SIZES.get(tier, 25.0)
    entry_price = market.yes_price if direction == "YES" else (1 - market.yes_price)

    position = Position(
        market=market.question,
        platform=market.platform,
        direction=direction,
        entry_price=entry_price,
        size=size,
        resolution_date=market.resolution_date,
        resolution_trigger=market.resolution_criteria[:200],
        status="OPEN",
        pnl=0.0,
        opened_at=datetime.now(timezone.utc).isoformat(),
        sim_confidence=sim_confidence,
        tier=tier,
    )

    positions = _load_positions()
    positions.append(_position_to_dict(position))
    _save_positions(positions)

    _print_position_log(position)
    return position


def _print_position_log(position: Position) -> None:
    border = "═" * 55
    log.info(f"\n{border}")
    log.info(f"  POSITION LOG — {datetime.now().strftime('%Y-%m-%d %H:%M ET')}")
    log.info(f"{border}")
    log.info(f"  Market:    {position.market[:50]}")
    log.info(f"  Platform:  {position.platform}")
    log.info(f"  Direction: LONG {position.direction}")
    log.info(f"  Entry:     ${position.entry_price:.4f}")
    log.info(f"  Size:      ${position.size:.0f} (Tier {position.tier})")
    log.info(f"  Resolves:  {position.resolution_date}")
    log.info(f"  Sim conf:  {position.sim_confidence:.0%}")
    log.info(f"  Status:    OPEN")
    log.info(f"  P&L:       $0.00")
    log.info(f"  ID:        {position.position_id}")
    log.info(f"{border}")


# ------------------------------------------------------------------ #
#  Exit monitoring                                                     #
# ------------------------------------------------------------------ #

def check_exits(position: Position, current_price: float) -> str:
    """
    Returns: HOLD | TAKE_PARTIAL_PROFIT | FLAG_STOP_LOSS | EXIT_NOW
    """
    entry = position.entry_price
    price_change = (current_price - entry) / entry if entry > 0 else 0

    if price_change >= 0.20:
        log.info(f"[Steven] +20% profit trigger on {position.position_id}: {price_change:.1%}")
        return "TAKE_PARTIAL_PROFIT"
    if price_change <= -0.30:
        log.info(f"[Steven] -30% stop-loss trigger on {position.position_id}: {price_change:.1%}")
        return "FLAG_STOP_LOSS"
    return "HOLD"


# ------------------------------------------------------------------ #
#  Close Position                                                      #
# ------------------------------------------------------------------ #

def close_position(position: Position, final_price: float) -> Position:
    """
    Mark position as closed and calculate final P&L.
    """
    pnl = (final_price - position.entry_price) * position.size
    position.status = "CLOSED"
    position.pnl = pnl
    position.closed_at = datetime.now(timezone.utc).isoformat()

    positions = _load_positions()
    for i, p in enumerate(positions):
        if p.get("position_id") == position.position_id:
            positions[i] = _position_to_dict(position)
            break
    _save_positions(positions)

    result = "WIN" if pnl > 0 else "LOSS"
    log.info(f"[Steven] Position {position.position_id} CLOSED: {result} | P&L=${pnl:.2f}")

    # Store calibration record in Pinecone long-term memory
    try:
        if _mem:
            slug = re.sub(r"[^a-z0-9]+", "-", position.market.lower())[:50].strip("-")
            pnl_str = f"+${abs(pnl):.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
            lesson = (
                f"Direction: {position.direction}. "
                f"Entry: {position.entry_price:.4f}. "
                f"Exit: {final_price:.4f}. "
                f"Tier {position.tier}. "
                f"Sim confidence was {position.sim_confidence:.0%}."
            )
            _mem.store_calibration(
                market_slug=slug,
                outcome=result,
                pnl=pnl_str,
                sim_confidence=position.sim_confidence,
                lesson=lesson,
                date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                direction=position.direction,
                tier=f"T{position.tier}",
            )
            _mem.store_agent_note(
                agent="Steven",
                note=f"Closed {result}: {position.market[:80]} | {pnl_str} | conf={position.sim_confidence:.0%}",
                market_slug=slug,
                note_type="trade-close",
            )
    except Exception as _exc:
        log.warning(f"[Steven] Pinecone store_calibration failed (non-fatal): {_exc}")

    return position


def get_open_positions() -> list[dict]:
    """Return all open positions from file."""
    return [p for p in _load_positions() if p.get("status") == "OPEN"]


def _position_to_dict(p: Position) -> dict:
    return {
        "position_id": p.position_id,
        "market": p.market,
        "platform": p.platform,
        "direction": p.direction,
        "entry_price": p.entry_price,
        "size": p.size,
        "resolution_date": p.resolution_date,
        "resolution_trigger": p.resolution_trigger,
        "status": p.status,
        "pnl": p.pnl,
        "opened_at": p.opened_at,
        "closed_at": p.closed_at,
        "sim_confidence": p.sim_confidence,
        "tier": p.tier,
    }
