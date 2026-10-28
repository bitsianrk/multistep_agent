# multistep_agent

Multi-Step Campaign Planning Agent for Tesco campaign managers. A chat message starts a run.
Agents and the human then work through five checkpointed stages and finish with an
**Agreed Brief** (brand, audience, PSGs) for the external Recommendation Draft process.

- **Skills** (`campaign_agent/skills/*/SKILL.md`) tell each agent what to do. They become the
  agent's system prompt.
- **The harness** (`campaign_agent/harness/`) runs a skill as a ReAct loop on Tesco's LRM:
  - state- and memory-aware prompts;
  - tool dispatch;
  - output guards;
  - a deterministic fallback.
- **Tools** (`campaign_agent/tools/library/`) are the typed capabilities the agents call.
- **The LRM client** uses the same API setup as the PR coupon design agent: the EA Garage
  `v1/lrm/` endpoint, `X-API-Key`, and text-based `TOOL:`/`ARGS:` tool calling.
- **Orchestration** is LangGraph, with an interrupt after every stage, checkpointed state,
  and working plus episodic memory.

It runs on **seeded dummy data**: every brand, number and feed is fictional.

Design and code map: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

## Quick start

```bash
pip install -r requirements.txt
```

Run the scripted demo:

```bash
python -m campaign_agent.demo
```

Start the interactive chat window:

```bash
python -m campaign_agent
```

Run the tests:

```bash
python -m pytest -q
```

Run the evaluation scorecard:

```bash
python -m campaign_agent.evaluation
```

Without credentials, the agents run on the offline **simulated model**. It drives the real
harness loop: prompts, tool calls, parsing, guards and memory. Its decisions equal the
deterministic policies.

### Connecting to the Tesco LRM

On the Tesco network, set the same variables the coupon design agent uses. They come from
the environment only, so never commit them (see `.env.example`):

```bash
export LRM_EMPLOYEE_ID=<your employee id>
```

```bash
export LRM_API_KEY=<your key>
```

Then start the chat. `--lrm-mode auto` goes live when both variables are set:

```bash
python -m campaign_agent --lrm-mode auto
```

Use `--lrm-mode off` for fully deterministic agents. In the CLI, `/harness` shows which
skills ran on the LRM and which fell back (and why), and `/memory` shows working and
episodic memory.

## Talking to it

| You say | It does |
|---|---|
| "yes" / "go" / "looks good" | approve, next stage |
| "What is SPC for light shoppers?" | answers from the pack or runs one more cut |
| "drop the heavy cohort", "yes, but lose the heavies", "make sharing bags a hero", "add sentiment_scan", "note: …" | edit |
| "try Snapjack instead", "send back to plan" | send back |
| "pause" | pause (with `--db runs/state.db`, resume later with `--run-id`) |

## Layout

```
campaign_agent/
  skills/             16 agent skills (SKILL.md) + HARNESS.md shared rules   <- what agents do
  harness/            ReAct runner, skill loader, state/memory context, schema + faithfulness guards
  llm/                Tesco LRM client (v1/lrm/, TOOL:/ARGS:), simulated + scripted transports
  agents/             roster + allow-lists, deterministic policies, harnessed.py (skill <-> harness bindings)
  tools/              28 tools (TOOL.md + manifest.yaml + golden tests), registry, MCP domain servers
  graph.py            LangGraph workflow: 5 stages, a checkpoint after each
  session.py, cli.py  chat window backend and terminal UI
  memory.py           episodic memory; overrides re-weight scoring
  data/               seeded synthetic feature tables, live-API snapshots, Kantar cache, taxonomy
  guardrails.py, handoff.py, evaluation.py, mcp_server.py, demo.py
```
