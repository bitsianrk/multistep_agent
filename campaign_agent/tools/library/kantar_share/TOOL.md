# kantar_share

Block 7. Kantar / Worldpanel value share for the latest release period, 3-period trend, and trend relative to the category.

## When to use
For market momentum and the planner's 'share falling' signal.

## When not to use
For household penetration (use kantar_penetration).

## Inputs
- `brand_id` (str)

## Outputs
- `brand_id` (str)
- `period` (str)
- `period_end` (str)
- `licence_scope` (str)
- `value_share` (float)
- `value_share_trend_pts` (float)

## Caveats
Cached per release period. Every figure carries period and licence_scope; panel detail is stripped before chat display.

Sources: kantar_cache - freshness: Per release period.
Binding today: dummy data (`campaign_agent.tools.servers.market:kantar_share`). Target binding: kantar_* MCP tool set over the licensed Worldpanel feed (cached per period).
