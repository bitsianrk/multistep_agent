# reconcile_metrics

Critic self-verification: cohort shares sum to 100%, shoppers x SPC ~= sales, frequency x spend per trip = SPC, Kantar period matches the window.

## When to use
After every Diagnostic Pack build, before the human sees it.

## When not to use
-

## Inputs
- `brand_id` (str)
- `pack` (dict)

## Outputs
- `brand_id` (str)
- `passed` (bool)
- `checks` (list)

## Caveats
-

Sources: none (pure function) - freshness: -.
Binding today: dummy data (`campaign_agent.tools.servers.planning:reconcile_metrics`). Target binding: Critic tool.
