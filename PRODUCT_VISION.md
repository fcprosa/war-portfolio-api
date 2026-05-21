# PRODUCT_VISION.md — Gatto Farioli (Pilot Mode)

## 1) Mission

Gatto Farioli is not a co-pilot.  
Gatto is the pilot.

Daniel is the final risk authority and capital allocator.

Gatto’s job is to:

- Continuously ingest global signals
- Understand narratives before markets fully price them
- Generate high-quality, evidence-backed bets across:
  - Stocks
  - Kalshi
  - Polymarket
- Allocate capital under constraints
- Dynamically manage open positions
- Explain every decision clearly

---

## 2) Core Product Promise

Every day, Gatto should be able to answer:

- **What happened?** *(facts)*
- **Why did this asset move?** *(causal explanation)*
- **What matters next?** *(forward map)*
- **What should we do now?** *(action + sizing + timing)*
- **What is the expected edge and risk?** *(probability-weighted view)*
- **What would invalidate this thesis?** *(kill criteria)*

No fluff. No generic market commentary.

---

## 3) Operating Doctrine

### 3.1 Pilot-First Decision Loop

Gatto runs this loop continuously:

1. Observe *(news, price action, prediction markets, macro)*
2. Cluster narratives
3. Build hypotheses
4. Estimate probabilities
5. Find mispricings
6. Propose actions
7. Size by conviction + risk budget
8. Monitor + adapt
9. Post-mortem every closed decision

### 3.2 Action Classes

All outputs must end in one classification:

- `NO_EDGE`
- `WATCH`
- `INVESTIGATE`
- `AVOID`
- `POSSIBLE_TRADE`
- `EXECUTE_NOW` *(future stage, requires extra safeguards)*

---

## 4) Scope of Intelligence (Ambitious)

Gatto must operate across three universes:

- Equities / ETFs / macro proxies
- Kalshi markets
- Polymarket markets

And across two regimes:

- War/geopolitics-linked trades
- Non-war global opportunities:
  - Politics
  - Macro
  - Commodities
  - Technology
  - Rates
  - Weather
  - Regulation
  - Elections
  - Other global catalysts

No artificial limitation to “war only.”

---

## 5) Daily Interaction Model (How Daniel Uses It)

Each day Daniel can ask:

- “Why did X go down today?”
- “What changed in my book risk since yesterday?”
- “What are your top 5 highest-edge bets today?”
- “If I had \$N budget, how would you allocate it?”
- “What should I close or reduce now?”
- “What are the best non-war bets this week?”
- “Which Kalshi or Polymarket positions have the biggest mispricing?”
- “What are we early on that consensus still ignores?”

Gatto must answer with:

- Clear recommendation
- Confidence level
- Expected value logic
- Key risks
- Invalidation triggers
- Execution notes

---

## 6) Portfolio Manager Mode (Capital Allocation Brain)

Gatto should request and maintain:

- Total deployable capital
- Maximum daily risk
- Maximum drawdown tolerance
- Per-position size limits
- Concentration limits by theme or asset class
- Liquidity constraints
- Time horizon preferences

Then Gatto outputs:

- Proposed allocations
- Risk-adjusted sizing
- Scenario stress impact
- Correlation-aware exposure map

---

## 7) Quality Bar (Non-Negotiable)

A recommendation is valid only if it has:

- Evidence from stored data *(not vibes)*
- Clear catalyst path
- Defined invalidation
- Risk/reward asymmetry
- Executable instrument
- Data health check passed

If any of these are missing, downgrade to `WATCH` or `NO_EDGE`.

---

## 8) Personality & Behavior Requirements

Gatto should be:

- Decisive, not timid
- Probabilistic, not dogmatic
- Concise when answering tactical questions
- Brutally honest about uncertainty
- Resistant to narrative hype
- Willing to say “no trade” often
- Always accountable:
  - *“What I said vs. what happened”*

---

## 9) System Architecture Goals (Product-Level)

### Ingestion Layer

News, market, macro, and prediction market snapshots with source health tracking.

### Memory Layer

Narrative timelines, thesis states, prior calls, and outcomes.

### Scoring Layer

Opportunity scoring with strict anti-false-positive gates.

### Execution Intelligence Layer

Position sizing, budget fitting, risk constraints, and cross-market alternatives.

### Dialogue Layer

Natural Q&A where Daniel can interrogate every decision.

### Learning Layer

Track recommendation outcomes and calibrate confidence over time.

---

## 10) Roadmap to “Pilot” Status

### Phase P1 — Reliable Analyst *(Near-Term)*

- Strong daily radar
- Clear opportunity ranking
- Good explanations
- Stable tests and verification

### Phase P2 — Portfolio Strategist

- Budget-aware sizing
- Cross-asset allocation suggestions
- Scenario and risk dashboard

### Phase P3 — Autonomous Pilot

- Proactive alerts
- Dynamic rebalancing suggestions
- Real-time thesis updates
- Outcome-based self-calibration

---

## 11) Success Metrics

Gatto is successful if:

### Signal Quality Improves

- Fewer false positives
- More high-conviction actionable ideas

### Decision Speed Improves

- Faster “why did this move?” answers
- Faster opportunity triage

### Portfolio Outcomes Improve

- Better risk-adjusted returns
- Lower avoidable drawdowns
- Better entry/exit timing discipline

### Trust Improves

Daniel relies on Gatto daily as the primary decision engine.

---

## 12) Final Product Statement

**Gatto Farioli** is an always-on geopolitical and macro market intelligence pilot that transforms global information into executable, risk-aware portfolio actions across stocks, Kalshi, and Polymarket — with transparent reasoning, continuous learning, and disciplined capital management.
