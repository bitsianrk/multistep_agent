# multistep_agent

Multi-Step Campaign Planning Agent for Tesco campaign managers. A chat message starts a run.
Agents and the human then work through five checkpointed stages and finish with an
**Agreed Brief** (brand, audience, PSGs) for the external Recommendation Draft process.

It is built with LangGraph (an interrupt after every stage) and a versioned skill registry
that can also be served over MCP. It runs on **seeded dummy data**: every brand, number and
feed is fictional.

Design and code map: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

## Quick start

```bash
pip install -r requirements.txt
```

Run the scripted demo:

```bash
python -m campaign_agent.demo
```

Run the scripted clarification demo:

```bash
python -m campaign_agent.demo --scenario clarify
```

Start the interactive chat window:

```bash
python -m campaign_agent
```

Run the tests, including every skill's golden cases:

```bash
python -m pytest -q
```

Run the evaluation scorecard:

```bash
python -m campaign_agent.evaluation
```

The default demo shows the full flow:

1. *"Plan a Multi-Step for Crunchwell crisps to win back lapsed shoppers, 4 weeks from 16 Nov"*
   resolves to one brand, objective and window.
2. The planner sees Kantar share falling and adds a lapsed-shopper analysis.
3. Ten diagnostic blocks run in parallel and reconcile 9/9.
4. You ask *"What is SPC for lapsed shoppers only?"*, which runs one more skill.
5. Brand Scout and two of the three advocates argue for **Snapjack**. Approving the switch
   re-runs the pack for Snapjack.
6. The Judge logs an off-scope reward suggestion as a note for the draft.
7. Your override (*"include medium"*) beats the Judge, and the Agreed Brief is handed off.

## Talking to it

At any checkpoint, reply in plain language:

| You say | It does |
|---|---|
| "yes" / "go" / "looks good" | approve, next stage |
| "What is SPC for light shoppers?" | answers from the pack or runs one more cut |
| "drop the heavy cohort", "exclude tortilla chips", "make sharing bags a hero", "add sentiment_scan", "objective grow", "note: …" | edit |
| "try Snapjack instead", "send back to plan" | send back |
| "pause" | pause (with `--db runs/state.db`, resume later with `--run-id`) |

Slash commands in the CLI: `/plan /pack /decision /brief /trace /audit /help /quit`.
Use `--autonomy L2` to auto-advance stages 1–3 when confidence is high.

## Layout

```
campaign_agent/
  graph.py            LangGraph workflow: 5 stages, a checkpoint after each
  session.py          CampaignSession: start / reply / state (the chat window's backend)
  cli.py, demo.py     terminal chat window, scripted scenarios
  agents/             entity resolution, planner, 6 skill agents + audience, debate + judge, gateway, brief writer
  skills/registry.py  allow-lists, role scope, typed contracts, SLA timeouts, budget, provenance
  skills/library/     28 skills: SKILL.md + manifest.yaml + tests/golden.json each
  skills/servers/     bindings, one MCP domain server each (transactions, commercial, market, ops, taxonomy, planning, chat)
  data/               seeded synthetic feature tables, live-API snapshots, Kantar cache, sentiment, taxonomy
  guardrails.py       access check, Kantar licence stripping
  memory.py           episodic memory; overrides re-weight scoring
  handoff.py          Agreed Brief sink (file by default; external process is out of scope)
  mcp_server.py       serve skills as MCP tools
  evaluation.py       scorecard
scripts/update_golden.py   regenerate / check golden snapshots
```
