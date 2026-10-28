# Multi-Step Campaign Planning Agent: Workflow & Architecture

This is the implementation of the *Campaign Planning Agent: Workflow & Architecture* plan
(Oct 28, 2026). It covers the whole Phase 1 MVP (chat, checkpoints, diagnostics, PSG and
audience picks) and the Phase 2 pieces (Kantar skills, debate panel and judge, Brand Scout),
all running on **seeded synthetic data**. Every brand, number and feed is fictional.

The run starts from a chat message and ends at an **Agreed Brief**: target brand, audience
and PSGs. The Brief goes to the external process that builds the Recommendation Draft.

## End-to-end workflow

![Workflow](images/workflow.png)

| Stage | Agent(s) | Human checkpoint | Output | Code |
|---|---|---|---|---|
| 1 · Understand | Entity Resolution Agent | confirms brand and objective, answers clarifications | one brand ID, objective, window | `agents/understand.py`, node `understand` / `cp_understand` |
| 2 · Plan | Campaign Planning Agent | approves or edits the skill plan before it runs | skill plan | `plan_campaign` skill, node `plan` / `cp_plan` |
| 3 · Diagnose | 6 skill agents in parallel + Audience Agent, then Critic | questions numbers, asks for extra cuts | Diagnostic Pack | `agents/diagnose.py`, node `diagnose` / `cp_diagnose` |
| 4 · Discuss | Reach, Margin, Customer advocates + Judge, human as participant | adds a position, overrides, sends back | decisions on brand, audience, PSGs | `agents/debate.py`, nodes `propose` / `cp_proposals` / `debate` / `cp_discuss` |
| 5 · Agreed Brief | Brief Writer | approves, which triggers the handoff | Agreed Brief (JSON + summary) | `write_brief` skill, node `brief` / `cp_brief` / `handoff` |
| External | Recommendation Draft process | n/a | Recommendation Draft | `handoff.py` (file sink by default) |

When a brand switch is agreed in Discuss, the run goes back to Diagnose and the pack is
rebuilt for the new brand. The brand is then locked (`max_brand_switches`).

### What the human can do at any checkpoint

The Chat Gateway's `route_message` skill classifies every reply into one of these actions.
The chat text is data and maps onto a closed set of edit operations.

| Action | Example | Effect |
|---|---|---|
| Approve | "Plan looks right, go" | moves to the next stage |
| Ask | "What is SPC for lapsed shoppers only?" | the stage's agent answers from the pack, or calls one more skill (`brand_spc(cohort=…)`) and adds it to `pack.extra_cuts` |
| Edit | "Drop the heavy cohort", "exclude tortilla chips", "make sharing bags a hero", "add sentiment_scan", "objective grow", "note: …" | updates state and re-validates, then shows the same checkpoint again |
| Send back | "Try Snapjack instead", "send back to plan" | jumps to an earlier stage with the new input (`resume_from_stage` says what to clear) |
| Pause | "pause" | state stays checkpointed; with `--db` the run resumes in a later session |

## Diagnostic Pack

| # | Block | Skills | Agent |
|---|---|---|---|
| 1 | Brand shoppers | `brand_shoppers` (+ `cut=lapsed_recency` when Kantar share is falling) | Brand Diagnostics |
| 2 | SPC | `brand_spc` (optional cohort filter) | Brand Diagnostics |
| 3 | Cohort split | `cohort_split` | Brand Diagnostics |
| 4 | Spend distribution | `spend_distribution` | Brand Diagnostics |
| 5 | Qualifying PSGs | `psg_score`, `stock_check`, `promo_clash_check` | Qualifying PSG |
| 6 | Seasonal strength | `seasonality_index` (+ `level=psg` for peak windows) | Performance Memory |
| 7 | Market sentiment | `kantar_share`, `kantar_penetration`, `sentiment_scan` | Market Intelligence |
| 8 | Profitability | `margin_after_funding`, `supplier_funding_lookup` | Commercial |
| 9 | Multi-Step history | `multistep_history` | Performance Memory |
| 10 | Brand alternatives | `category_brands`, `brand_scout_score` | Brand Scout |

Each block carries provenance (`source`, `as_of`) from the skill manifests, and the Brief lists
it per block. The Critic's `reconcile_metrics` checks every pack before the human sees it:
cohort shares sum to 100%, shoppers × SPC ≈ brand-week sales, frequency × spend per trip = SPC,
block 1 and block 2 shopper counts agree, the lapsed pool agrees with the cohort split, and the
Kantar period lines up with the diagnostics window.

**PSG rules** (`QualifyingPSGAgent.propose`). The default basket is the whole brand. Hard
filters exclude PSGs below the stock-cover threshold, PSGs on a live promotion overlapping the
window (double discount), and PSGs below the margin floor. Hero PSGs are ranked by
reach × momentum, multiplied by PSG seasonality when the window covers peak weeks.

**Audience** (`audience_propose`). Each cohort gets an objective prior plus a past-response
term plus a learned bias from earlier human overrides. Cohorts below the minimum cell size
cannot be included.

**Brand Scout** (`brand_scout_score`). It runs on every request and z-scores seasonal fit,
Kantar momentum, sentiment trend, headroom and margin within the category. An alternative
"beats" the requested brand only with a score lead and a better seasonal fit or momentum.

## Agents and skills

Agents own judgement and skills own capability. `agents/base.py:ROSTER` holds each agent's
persona and allow-list, and the registry refuses any call outside it. There are 28 skills in
`campaign_agent/skills/library/<skill>/`:

```
SKILL.md           when to use / when not / inputs / outputs / caveats (what the planner reads)
manifest.yaml      owner, version, typed inputs + outputs, binding, sources, freshness,
                   latency SLA, access scope, mechanics enabled
tests/golden.json  golden cases (snapshot + hand-labelled), run in CI
```

Bindings are grouped as one MCP server per domain in `skills/servers/`: `transactions`,
`commercial`, `market`, `ops`, `taxonomy`, `planning` and `chat`. Each manifest records the
production `target_binding` (Databricks SQL, Kantar feed, live APIs) next to today's dummy
binding. `python -m campaign_agent.mcp_server --domain market` serves the same skills to
notebooks or an analyst assistant.

The registry (`skills/registry.py`) enforces the following on every call:

- the agent's allow-list;
- the role in `access_scope` (internal campaign managers only);
- the typed input contract and the output contract;
- the latency SLA as a timeout;
- the per-run skill-call budget.

It also attaches provenance to every result.

## Data layer

`data/generator.py` builds everything from a seed. Household-level rows come first, and the
weekly tables are calibrated to them, so reconciliation is real arithmetic:

| Design table / feed | Dummy implementation |
|---|---|
| `feat_customer_brand_6w` | about 17k household × brand rows: trips, spend, prior 6 weeks, PSG split, loyalty tier |
| `feat_brand_week`, `feat_psg_week` | 156 weeks with category seasonality and brand / PSG trends |
| `feat_multistep_outcome` / campaign mart | 2 past campaigns per brand, response by cohort |
| Segmentation snapshot | lifestage, value segment, Clubcard Plus per household |
| Stock API, promotions API, price API | snapshot at T-1 09:00 (cover weeks, live promos, shelf price) |
| Commercial mart | margin and supplier funding per PSG |
| Kantar / Worldpanel | 13 four-weekly periods; cached per release period; `licence_scope` on every figure |
| Sentiment feed | 90 days of daily net sentiment |
| Product taxonomy | `data/taxonomy.py`: 3 categories, 9 fictional brands, 45 PSGs |

## What makes it agentic

| Capability | Where |
|---|---|
| Chat trigger and control surface | `session.py`, `cli.py` |
| Plan per brand | `plan_campaign` adds lapsed-shopper analysis when Kantar share falls, objective-specific cuts, and PSG seasonality for peak windows |
| Tool choice within allow-lists | `ROSTER` + registry |
| Clarification | Entity Resolution asks back on ambiguous brands ("Golden") or a missing objective |
| Self-verification | Critic `reconcile_metrics`; Judge checks that claim citations exist in the pack |
| Challenge the premise | Brand Scout plus the advocates can win a switch, which re-runs the pack |
| Human as participant | a checkpoint after every stage; the human's position joins the debate; the human's final call overrides the Judge |
| Memory | episodic (`memory.py`: runs and overrides), semantic (taxonomy), procedural (skill library) |
| Learning | overrides re-weight audience (`cohort_bias`), hero PSGs (`hero_bias`) and Brand Scout (`switch_bias`) for the category |

**Checkpoints** (`graph.py`). Each `cp_*` node calls LangGraph `interrupt()`. State is
checkpointed (in memory, or SQLite with `--db`), and the reply resumes the graph with
`Command(resume=…)`.

**Debate protocol.** Three advocates propose, citing blocks. The human may add a position.
Up to `max_debate_rounds` critique rounds follow: a minority position concedes when its
strength is below the round's threshold and holds otherwise. The panel converges when the
share of unanimous items reaches `convergence_tolerance`. Off-scope claims (thresholds,
rewards, coupons…) are rejected by `scope_check` and logged in `notes_for_draft`. Remaining
dissent goes into the Brief.

**Autonomy ladder.** `autonomy_level="L1"` (default) waits at every checkpoint. `"L2"`
auto-advances stages 1–3 when confidence is high: the brand is resolved with no assumed
window, and the pack reconciled with no errors.

## Guardrails

| Risk | Control |
|---|---|
| Access beyond campaign managers | `guardrails.authorize` at session start; `access_scope` on every skill call |
| Kantar licence | period + `licence_scope` on each figure; `display_safe` strips raw panel fields before chat or MCP output |
| Customer privacy | aggregates only; `min_cell_size` suppression in cohort splits and sizes |
| Discussion drift | Judge `scope_check`; off-scope claims become notes for the draft |
| Hallucinated numbers | answers and claims only quote pack blocks or fresh skill calls; Critic reconciliation; provenance per block |
| Prompt injection | chat text maps onto a closed edit vocabulary; plan edits are limited to registry skills; core blocks 1–3 cannot be removed |
| Runaway cost / loops | `max_debate_rounds`, `max_send_backs`, `max_brand_switches`, `max_skill_calls`, skill SLAs |

## Evaluation

`python -m campaign_agent.evaluation` reports three things:

- **Metric accuracy:** skill outputs against an independent "certified" recomputation.
- **Brand resolution accuracy:** on a labelled request set, including an ambiguous request that
  must ask back.
- **Run metrics from the audit log:** override rate by stage, Brand Scout acceptance, scope
  adherence, faithfulness of citations, time to Agreed Brief, and skill calls.

## Moving to production (not done here)

- Swap each manifest's `binding` for its `target_binding`: Databricks SQL on the feature
  tables, the licensed Kantar feed, and the live stock, promo and price APIs. The registry,
  agents and graph do not change.
- Put the advocates' and Brief Writer's wording behind the approved LLM endpoint. Keep the
  numbers coming only from the pack: today all text is templated and deterministic.
- Replace the CLI with the Teams bot / web chat. `CampaignSession` is the seam.
- Move checkpoints to PostgreSQL or Cosmos DB, add OpenTelemetry tracing (the `trace` list
  already records agent, skill, version and latency per call), and use Entra ID for `user.role`.
- Settle the open questions in the plan: the Brief transport (the `HandoffSink` protocol), the
  canonical cohort framework, the Brand Scout cap (`scout_max_alternatives`), and the brand
  switch rules.
