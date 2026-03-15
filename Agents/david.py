"""
David — Engineer
Steps: 5 (run MiroFish simulation × 3), 10 (post-resolution calibration log)
"""

import csv
import json
import logging
import re
import statistics
from datetime import datetime
from pathlib import Path

import anthropic
from typing import Tuple

from mirofish_client import MiroFishClient, MiroFishError
from models import Market, Position, SimResult

log = logging.getLogger("david")

CALIBRATION_LOG = Path(__file__).parent.parent / "data" / "calibration_log.csv"
MODEL = "claude-sonnet-4-6"

SYSTEM_POSTMORTEM = """You are David, an engineer and quantitative analyst.
A prediction market simulation just resolved. Write a one-sentence calibration lesson
that Alba can use to improve the next seed file for markets in this category.

Return ONLY valid JSON:
{
  "seed_quality": "Good|Gaps|Stale sources",
  "prompt_matched_criteria": true|false,
  "agent_mix_realistic": true|false,
  "lesson": "one specific actionable improvement for the next simulation in this category"
}
"""


def _extract_confidence(markdown: str) -> Tuple[float, str]:
    """
    Parse confidence % and direction from MiroFish markdown report.
    Looks for patterns like '73%', 'YES: 73', 'confidence: 0.73', 'probability: 73%'
    Returns (confidence_float, direction_str).
    """
    text = markdown.lower()

    # Try to find explicit YES/NO probability statements
    yes_match = re.search(r"yes[^\d]*(\d{1,3})\s*%", text)
    no_match  = re.search(r"no[^\d]*(\d{1,3})\s*%", text)
    conf_match = re.search(r"(?:probability|confidence|likelihood)[^\d]*(\d{1,3})\s*%", text)
    plain_pct  = re.search(r"(\d{1,3})\s*%", text)

    if yes_match and no_match:
        yes_val = int(yes_match.group(1)) / 100
        no_val  = int(no_match.group(1)) / 100
        return (yes_val, "YES") if yes_val >= no_val else (no_val, "NO")

    if yes_match:
        val = int(yes_match.group(1)) / 100
        return val, "YES"
    if no_match:
        val = int(no_match.group(1)) / 100
        return val, "NO"
    if conf_match:
        val = int(conf_match.group(1)) / 100
        # Default direction from sentiment words
        direction = "YES" if "bullish" in text or "likely yes" in text else "NO"
        return val, direction
    if plain_pct:
        val = int(plain_pct.group(1)) / 100
        return val, "YES"

    log.warning("Could not extract confidence from report — defaulting to 0.5 / YES")
    return 0.5, "YES"


# ------------------------------------------------------------------ #
#  Step 5 — Run MiroFish simulation (3 runs)                         #
# ------------------------------------------------------------------ #

def run_simulation(
    market: Market,
    seed_path: Path,
    sim_prompt: str,
    mirofish_url: str = "http://localhost:5001",
) -> SimResult:
    """
    Step 5: Run MiroFish 3× and return averaged SimResult.
    Raises MiroFishError if variance > 15% or fewer than 3 runs complete.
    """
    client = MiroFishClient(base_url=mirofish_url)

    if not client.ping():
        raise MiroFishError("MiroFish backend is not reachable. Is Docker running?")

    log.info(f"[Step 5] David running 3 MiroFish simulations for: {market.question[:60]}")

    run_results = []
    for run_num in range(1, 4):
        log.info(f"[Step 5] Run {run_num}/3 starting...")
        try:
            project_name = f"{market.slug}-run{run_num}"
            sim_id, report_id, markdown = client.full_run(
                seed_txt_path=seed_path,
                simulation_requirement=sim_prompt,
                project_name=project_name,
            )
            confidence, direction = _extract_confidence(markdown)
            log.info(f"[Step 5] Run {run_num} complete: {direction} @ {confidence:.0%}")
            run_results.append((sim_id, report_id, markdown, confidence, direction))
        except MiroFishError as e:
            log.error(f"[Step 5] Run {run_num} failed: {e}")
            continue

    if len(run_results) < 3:
        raise MiroFishError(
            f"Only {len(run_results)}/3 simulation runs completed. Self-blocking per protocol."
        )

    confidences = [r[3] for r in run_results]
    variance = statistics.stdev(confidences) if len(confidences) > 1 else 0.0
    if variance > 0.15:
        raise MiroFishError(
            f"Run variance {variance:.2%} exceeds 15% threshold. Self-blocking per protocol. "
            f"Runs: {[f'{c:.0%}' for c in confidences]}"
        )

    avg_confidence = statistics.mean(confidences)
    # Direction by majority vote
    directions = [r[4] for r in run_results]
    direction = max(set(directions), key=directions.count)

    # Use the middle run's report as the canonical report
    _, report_id, markdown, _, _ = run_results[1]
    sim_id = run_results[1][0]

    result = SimResult(
        simulation_id=sim_id,
        report_id=report_id,
        confidence=avg_confidence,
        direction=direction,
        markdown=markdown,
        variance=variance,
        run_confidences=confidences,
    )
    log.info(
        f"[Step 5] SIMULATION RESULT: {direction} @ {avg_confidence:.0%} "
        f"(variance={variance:.2%}, runs={[f'{c:.0%}' for c in confidences]})"
    )
    return result


# ------------------------------------------------------------------ #
#  Step 10 — Post-resolution calibration log                         #
# ------------------------------------------------------------------ #

def log_resolution(
    position: Position,
    sim_result: SimResult,
    actual_outcome: str,   # "YES" or "NO"
) -> str:
    """
    Step 10: Log resolved market to calibration CSV and extract lesson.
    Returns calibration note string for Alba.
    """
    win_loss = "WIN" if sim_result.direction == actual_outcome else "LOSS"
    pnl = (1 - position.entry_price) * position.size if win_loss == "WIN" else -position.entry_price * position.size

    log.info(f"[Step 10] Resolution logged: {win_loss} | P&L=${pnl:.2f}")

    # Get post-mortem lesson from Claude
    client = anthropic.Anthropic()
    user = (
        f"Market: {position.market}\n"
        f"Category: prediction market\n"
        f"Sim confidence: {sim_result.confidence:.0%} | Sim direction: {sim_result.direction}\n"
        f"Actual outcome: {actual_outcome} | Result: {win_loss}\n"
        f"Report excerpt:\n{sim_result.markdown[:1500]}\n\n"
        "Provide calibration feedback."
    )
    response = client.messages.create(
        model=MODEL,
        max_tokens=512,
        system=SYSTEM_POSTMORTEM,
        messages=[{"role": "user", "content": user}],
    )
    pm_text = response.content[0].text.strip()
    pm_text = re.sub(r"^```(?:json)?\s*\n?", "", pm_text, flags=re.IGNORECASE)
    pm_text = re.sub(r"\n?```\s*$", "", pm_text)
    pm = json.loads(pm_text.strip())

    # Append to calibration CSV
    row = {
        "DATE": datetime.utcnow().strftime("%Y-%m-%d"),
        "MARKET": position.market[:80],
        "SIM_CONFIDENCE": f"{sim_result.confidence:.2%}",
        "SIM_DIRECTION": sim_result.direction,
        "ACTUAL_OUTCOME": actual_outcome,
        "WIN_LOSS": win_loss,
        "TIER": position.tier,
        "POSITION_SIZE": position.size,
        "PNL": f"{pnl:.2f}",
        "SEED_QUALITY": pm.get("seed_quality", ""),
        "LESSON": pm.get("lesson", ""),
    }
    write_header = not CALIBRATION_LOG.exists() or CALIBRATION_LOG.stat().st_size == 0
    with open(CALIBRATION_LOG, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(row.keys()))
        if write_header:
            writer.writeheader()
        writer.writerow(row)

    lesson = pm.get("lesson", "No lesson extracted.")
    log.info(f"[Step 10] Calibration note: {lesson}")
    return lesson
