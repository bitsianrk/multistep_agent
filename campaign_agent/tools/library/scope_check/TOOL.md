# scope_check

Judge scope enforcement: flags any claim outside brand, audience and PSGs (thresholds, rewards, coupons...).

## When to use
On every advocate and human claim in the discussion.

## When not to use
-

## Inputs
- `brand_id` (str)
- `claims` (list)

## Outputs
- `brand_id` (str)
- `results` (list)
- `in_scope_pct` (float)

## Caveats
Off-scope claims become notes for the external draft process.

Sources: none (pure function) - freshness: -.
Binding today: dummy data (`campaign_agent.tools.servers.planning:scope_check`). Target binding: Judge tool (LLM-as-judge in production).
