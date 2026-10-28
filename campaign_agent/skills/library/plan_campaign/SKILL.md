# plan_campaign

Builds the skill plan for a brand, objective and window, adding analyses the brand needs (e.g. share falling -> lapsed-shopper analysis).

## When to use
Stage 2 - the plan is shown to the human before it runs.

## When not to use
-

## Inputs
- `brand_id` (str)
- `objective` (str)
- `window_start` (date)
- `window_weeks` (int)

## Outputs
- `brand_id` (str)
- `steps` (list)
- `signals` (dict)

## Caveats
Only registry skills can appear in a plan.

Sources: kantar_cache, taxonomy - freshness: -.
Binding today: dummy data (`campaign_agent.skills.servers.planning:plan_campaign`). Target binding: Planner skill over the registry.
