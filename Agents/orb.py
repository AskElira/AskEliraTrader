"""
Orb — Operations Manager + Pipeline Coordinator
Step 7: Go/No-Go capital decision
Main: run_full_pipeline() orchestrates all 10 steps
"""

import logging
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path

from typing import Optional
from models import Market, Position, SimResult, VexVerdict

log = logging.getLogger("orb")

# ------------------------------------------------------------------ #
#  Tier assignment                                                     #
# ------------------------------------------------------------------ #

def _assign_tier(confidence: float) -> int:
    if confidence >= 0.90:
        return 3
    if confidence >= 0.80:
        return 2
    return 1


# ------------------------------------------------------------------ #
#  Step 7 — Go / No-Go                                               #
# ------------------------------------------------------------------ #

def go_no_go(
    market: Market,
    sim_result: SimResult,
    vex_verdict: VexVerdict,
    calendar_verdict: str,
) -> dict:
    """
    Step 7: Apply all decision gates.
    Returns dict: {approved, tier, direction, reason, blocked_by}
    """
    log.info("[Step 7] Orb running go/no-go decision gate...")

    gates = []

    # Gate 1: Confidence ≥ 70%
    if sim_result.confidence < 0.70:
        gates.append(f"FAIL confidence {sim_result.confidence:.0%} < 70%")
    else:
        gates.append(f"PASS confidence {sim_result.confidence:.0%}")

    # Gate 2: Vex verdict
    if vex_verdict.verdict == "FAIL":
        gates.append(f"FAIL vex={vex_verdict.verdict}")
    else:
        gates.append(f"PASS vex={vex_verdict.verdict}")

    # Gate 3: Calendar
    if calendar_verdict == "FLAGGED":
        gates.append("FAIL calendar=FLAGGED")
    else:
        gates.append(f"PASS calendar={calendar_verdict}")

    # Gate 4: Liquidity
    if market.liquidity < 500:
        gates.append(f"FAIL liquidity=${market.liquidity:.0f} < $500")
    else:
        gates.append(f"PASS liquidity=${market.liquidity:.0f}")

    # Gate 5: Override risk
    if vex_verdict.override_risk:
        gates.append("FAIL single-actor override risk flagged by Vex")
    else:
        gates.append("PASS no single-actor override risk")

    # Gate 6: Uncertainty
    if market.uncertainty == "HIGH":
        gates.append("FAIL Alba uncertainty=HIGH")
    else:
        gates.append(f"PASS uncertainty={market.uncertainty}")

    failed = [g for g in gates if g.startswith("FAIL")]
    approved = len(failed) == 0

    tier = _assign_tier(sim_result.confidence) if approved else 0

    for gate in gates:
        icon = "✓" if gate.startswith("PASS") else "✗"
        log.info(f"[Step 7]   {icon} {gate}")

    if approved:
        log.info(f"[Step 7] DECISION: APPROVED ✅ | Tier {tier} (${[25,25,50,100][tier]}) | LONG {sim_result.direction}")
    else:
        log.info(f"[Step 7] DECISION: BLOCKED ❌ | Reasons: {failed}")

    return {
        "approved": approved,
        "tier": tier,
        "direction": sim_result.direction,
        "blocked_by": failed,
        "gates": gates,
    }


# ------------------------------------------------------------------ #
#  Full pipeline orchestrator                                         #
# ------------------------------------------------------------------ #

def run_full_pipeline(today: Optional[str] = None) -> dict:
    """
    Orchestrate all 10 steps. Returns pipeline result dict.
    Designed to be called by the scheduler in loop.py.
    """
    from mirofish_client import MiroFishError
    import alba
    import david
    import vex
    import steven

    if today is None:
        today = date.today().isoformat()

    mirofish_url = os.environ.get("MIROFISH_URL", "http://localhost:5001")

    log.info("=" * 60)
    log.info(f"ORB PIPELINE START — {today}")
    log.info("=" * 60)

    # ── Step 1: Market scan ──────────────────────────────────────────
    log.info("[Orb] Step 1 → Alba: market scan")
    market = alba.scan_markets(today)
    if not market:
        log.info("[Orb] No qualifying market today. Pipeline complete.")
        return {"status": "no_market", "date": today}

    # ── Step 2: Calendar check ───────────────────────────────────────
    log.info("[Orb] Step 2 → Alba: calendar check")
    calendar_events, calendar_verdict = alba.check_calendar(market, today)
    if calendar_verdict == "FLAGGED":
        log.info(f"[Orb] ⚠️  Calendar FLAGGED — continuing but will gate at Step 7")

    # ── Step 3: Seed file ────────────────────────────────────────────
    log.info("[Orb] Step 3 → Alba: build seed file")
    seed_path = alba.build_seed_file(market, today)
    seed_text = seed_path.read_text(encoding="utf-8")

    # Parse source metadata from seed for Vex
    source_types = re.findall(r"^TYPE:\s*(.+)$", seed_text, re.MULTILINE)
    source_dates = re.findall(r"^DATE:\s*(.+)$", seed_text, re.MULTILINE)

    # ── Step 4: Simulation prompt ────────────────────────────────────
    log.info("[Orb] Step 4 → Alba: write simulation prompt")
    sim_prompt = alba.write_simulation_prompt(market, seed_text)

    # ── Step 5: Run MiroFish ─────────────────────────────────────────
    log.info("[Orb] Step 5 → David: run MiroFish simulation")
    try:
        sim_result = david.run_simulation(market, seed_path, sim_prompt, mirofish_url)
    except MiroFishError as e:
        log.error(f"[Orb] Step 5 BLOCKED: {e}")
        return {"status": "mirofish_error", "date": today, "error": str(e)}

    # ── Step 6: Vex audit ────────────────────────────────────────────
    log.info("[Orb] Step 6 → Vex: adversarial audit")
    vex_verdict = vex.audit(market, sim_result, source_types, source_dates)

    if vex_verdict.verdict == "FAIL":
        log.info("[Orb] Step 6 BLOCKED: Vex FAIL. Pipeline halted. Reseed required.")
        return {"status": "vex_fail", "date": today, "findings": vex_verdict.findings}

    # ── Step 7: Go / No-Go ───────────────────────────────────────────
    log.info("[Orb] Step 7 → Orb: go/no-go decision")
    decision = go_no_go(market, sim_result, vex_verdict, calendar_verdict)

    if not decision["approved"]:
        log.info(f"[Orb] BLOCKED: {decision['blocked_by']}")
        return {"status": "blocked", "date": today, "reason": decision["blocked_by"]}

    # ── Step 8: Open position ────────────────────────────────────────
    log.info("[Orb] Step 8 → Steven: open position")
    position = steven.open_position(
        market=market,
        direction=decision["direction"],
        tier=decision["tier"],
        sim_confidence=sim_result.confidence,
    )

    log.info("=" * 60)
    log.info(f"ORB PIPELINE COMPLETE — Position {position.position_id} OPEN")
    log.info(f"  Market:    {position.market[:55]}")
    log.info(f"  Direction: LONG {position.direction} @ ${position.entry_price:.4f}")
    log.info(f"  Size:      ${position.size:.0f} (Tier {position.tier})")
    log.info(f"  Sim conf:  {sim_result.confidence:.0%} | Vex: {vex_verdict.verdict}")
    log.info("=" * 60)

    return {
        "status": "position_opened",
        "date": today,
        "position_id": position.position_id,
        "market": position.market,
        "direction": position.direction,
        "size": position.size,
        "tier": position.tier,
        "confidence": sim_result.confidence,
        "vex": vex_verdict.verdict,
    }


def monitor_open_positions(today: Optional[str] = None) -> None:
    """
    Step 9: Run Alba's daily monitor on all open positions.
    Called by scheduler at 8:45 AM ET.
    """
    import alba
    import steven
    from models import Position

    if today is None:
        today = date.today().isoformat()

    raw_positions = steven.get_open_positions()
    if not raw_positions:
        log.info("[Orb] No open positions to monitor.")
        return

    log.info(f"[Orb] Monitoring {len(raw_positions)} open position(s)...")
    for p_dict in raw_positions:
        position = Position(**{k: p_dict.get(k) for k in Position.__dataclass_fields__})
        result = alba.monitor_position(position, today)
        action = result.get("action", "HOLD")

        if action == "HOLD":
            log.info(f"[Orb] {position.position_id}: HOLD ✅ — thesis valid")
        elif action == "FLAG_TO_ORB":
            log.warning(f"[Orb] ⚠️  {position.position_id}: FLAG_TO_ORB — {result.get('action_reason')}")
        elif action == "SIMULATE_AGAIN":
            log.warning(f"[Orb] 🔄 {position.position_id}: SIMULATE_AGAIN — {result.get('new_development')}")
        elif action == "EXIT_NOW":
            log.error(f"[Orb] 🚨 {position.position_id}: EXIT_NOW — {result.get('action_reason')}")
            log.error("[Orb] !! Manual exit required — Steven must close this position immediately !!")
