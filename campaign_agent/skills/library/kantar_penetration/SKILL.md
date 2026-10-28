# kantar_penetration

Block 7. Kantar / Worldpanel household penetration for the latest release period and its trend.

## When to use
Alongside kantar_share to read market momentum.

## When not to use
For Clubcard penetration (use brand_shoppers).

## Inputs
- `brand_id` (str)

## Outputs
- `brand_id` (str)
- `period` (str)
- `licence_scope` (str)
- `penetration` (float)
- `penetration_trend_pts` (float)

## Caveats
Same licence rules as kantar_share.

Sources: kantar_cache - freshness: Per release period.
Binding today: dummy data (`campaign_agent.skills.servers.market:kantar_penetration`). Target binding: kantar_* MCP skill set over the licensed Worldpanel feed.
