"""
Vex — Adversarial Auditor
Step 6: Tears apart MiroFish simulation output before capital moves.
"""

import json
import logging
import re

import anthropic

from models import Market, SimResult, VexVerdict

log = logging.getLogger("vex")

MODEL = "claude-sonnet-4-6"

SYSTEM_VEX = """You are Vex, an adversarial auditor for a prediction market trading operation.
Your job is to find every flaw in a simulation before real money is risked.
You are skeptical by default. Your role is to PROTECT capital, not approve trades.

Run this full audit checklist:

□ RESOLUTION MATCH
  Does the simulation output address the EXACT contract question, or drift to something adjacent?
  Any semantic drift = FAIL.

□ SEED FRESHNESS
  Are source dates provided? Any source older than 72 hours for a fast-moving market = FLAG.
  Missing source dates = FLAG.

□ CONFIDENCE CHECK
  Is confidence above 85%? If yes — is there specific simulation evidence that justifies it?
  Polymarket rarely misprices >85% unless near resolution. Flag overconfidence.

□ SINGLE ACTOR RISK
  Can one person's tweet, statement, or unilateral action flip this market outcome
  regardless of what the simulation says? If yes — flag as OVERRIDE RISK.

□ LOOK-AHEAD CHECK
  Did any seed source reference outcome information written AFTER the event began resolving?
  Any look-ahead contamination = FAIL, reseed required.

□ AGENT REALISM
  Did MiroFish's agent behavior reflect realistic actors for this specific market domain?
  (e.g., general public agents are wrong for an FOMC rate decision market)

Return ONLY valid JSON:
{
  "verdict": "PASS" or "PASS-WITH-WARNINGS" or "FAIL",
  "findings": ["finding 1", "finding 2"],
  "confidence": "HIGH" or "MEDIUM" or "LOW" or "DO NOT DEPLOY",
  "override_risk": true or false,
  "fail_reasons": ["reason if FAIL, else empty"]
}

FAIL triggers: resolution drift, look-ahead contamination, variance >15%.
PASS-WITH-WARNINGS: minor issues that don't block deployment but reduce confidence.
PASS: clean simulation, deploy at assigned tier.
"""


def audit(
    market: Market,
    sim_result: SimResult,
    seed_source_types: list[str],
    seed_source_dates: list[str],
) -> VexVerdict:
    """
    Step 6: Full adversarial audit of MiroFish simulation.
    Returns VexVerdict.
    """
    log.info(f"[Step 6] Vex auditing simulation for: {market.question[:60]}")

    client = anthropic.Anthropic()
    user = (
        f"MARKET QUESTION: {market.question}\n"
        f"RESOLUTION CRITERIA (verbatim): {market.resolution_criteria}\n"
        f"RESOLUTION DATE: {market.resolution_date}\n\n"
        f"SIMULATION DIRECTION: {sim_result.direction}\n"
        f"SIMULATION CONFIDENCE: {sim_result.confidence:.0%}\n"
        f"RUN VARIANCE: {sim_result.variance:.2%}\n"
        f"RUN CONFIDENCES: {[f'{c:.0%}' for c in sim_result.run_confidences]}\n\n"
        f"SEED SOURCE TYPES: {seed_source_types}\n"
        f"SEED SOURCE DATES: {seed_source_dates}\n\n"
        f"SIMULATION REPORT (first 3000 chars):\n{sim_result.markdown[:3000]}\n\n"
        "Run the full audit checklist now."
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=SYSTEM_VEX,
        messages=[{"role": "user", "content": user}],
    )
    raw = response.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*\n?", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\n?```\s*$", "", raw)
    data = json.loads(raw.strip())

    verdict = VexVerdict(
        verdict=data["verdict"],
        findings=data.get("findings", []),
        confidence=data.get("confidence", "MEDIUM"),
        override_risk=data.get("override_risk", False),
    )

    log.info(f"[Step 6] VEX VERDICT: {verdict.verdict} | confidence={verdict.confidence} | override_risk={verdict.override_risk}")
    for i, finding in enumerate(verdict.findings, 1):
        log.info(f"[Step 6]   Finding {i}: {finding}")

    return verdict
