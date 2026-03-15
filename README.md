# Polymarket MiroFish Agent Loop

Fully autonomous 10-step prediction market trading pipeline.
Scans Polymarket/Kalshi → runs MiroFish swarm simulation → audits → decides → logs.
Zero human input required after `python loop.py`.

## Architecture

```
Alba  → market scan + calendar + seed file + simulation prompt
David → MiroFish runner (3× runs) + calibration log
Vex   → adversarial audit (blocks bad simulations)
Orb   → go/no-go decision + pipeline coordinator
Steven → position logger + exit monitor
```

## Requires

- Python 3.11+
- [MiroFish](https://github.com/666ghj/MiroFish) running via Docker
- Anthropic API key

## Setup

```bash
# 1. Start MiroFish (if not already running)
cd /path/to/MiroFish && docker-compose up -d

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure
cp .env.example .env
# Edit .env — add your ANTHROPIC_API_KEY

# 4. Run
python loop.py           # scheduled daily at 09:00
python loop.py --once    # single pass immediately
python loop.py --monitor # check open positions now
```

## 10-Step Pipeline

| Step | Agent | Action |
|------|-------|--------|
| 1 | Alba | Market scan — finds best mispriced binary market |
| 2 | Alba | Economic calendar — flags high-impact events |
| 3 | Alba | Seed file — 6-8 sourced .txt for MiroFish |
| 4 | Alba | Simulation prompt — Box 02 natural language |
| 5 | David | MiroFish × 3 runs — averages confidence, checks variance |
| 6 | Vex | Adversarial audit — PASS / PASS-WITH-WARNINGS / FAIL |
| 7 | Orb | Go/No-Go — 6 gates, capital tier assignment |
| 8 | Steven | Position log — writes to active_positions.json |
| 9 | Alba | Daily monitor — premise validity check |
| 10 | David | Post-resolution — calibration log + lesson |

## Hard Gates (Step 7)

All must pass or pipeline blocks:
- MiroFish confidence ≥ 70%
- Vex verdict: PASS or PASS-WITH-WARNINGS
- Calendar: CLEAR (no high-impact events before resolution)
- Liquidity > $500
- No single-actor override risk
- Alba uncertainty ≠ HIGH

## Capital Tiers

| Tier | Confidence | Size |
|------|-----------|------|
| 1 | 70–79% | $25 |
| 2 | 80–89% | $50 |
| 3 | ≥ 90%  | $100 |

## Data Files

- `data/calibration_log.csv` — every resolved market with win/loss + lesson
- `data/active_positions.json` — open positions
- `data/seeds/` — Alba's seed .txt files (gitignored)
- `data/loop.log` — full run logs

## Agent Personas

Agent definitions live in `Agents/` — read by the Claude system prompts.
MiroFish simulation config in `claude.md`.

---

*Powered by [MiroFish](https://github.com/666ghj/MiroFish) swarm intelligence engine*
