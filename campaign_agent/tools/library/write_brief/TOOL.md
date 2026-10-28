# write_brief

Writes the Agreed Brief JSON (the handoff contract) and the chat summary.

## When to use
Stage 5.

## When not to use
-

## Inputs
- `brand_id` (str)
- `decision` (dict)
- `pack` (dict)
- `approved_by` (str?)

## Outputs
- `brief` (dict)
- `summary_md` (str)

## Caveats
-

Sources: none (pure function) - freshness: -.
Binding today: dummy data (`campaign_agent.tools.servers.planning:write_brief`). Target binding: Brief Writer tool.
