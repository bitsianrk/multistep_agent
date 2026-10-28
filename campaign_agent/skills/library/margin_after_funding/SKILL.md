# margin_after_funding

Block 8. Margin after supplier funding per PSG and sales-weighted for the brand; flags PSGs below the margin floor.

## When to use
For the PSG margin hard filter and the Brand Scout margin driver.

## When not to use
For supplier funding pools (use supplier_funding_lookup).

## Inputs
- `brand_id` (str)

## Outputs
- `brand_id` (str)
- `brand_margin_after_funding_pct` (float)
- `psgs` (list)

## Caveats
Margin after funding = base margin % + supplier funding % of sales.

Sources: commercial_mart, price_api - freshness: Daily.
Binding today: dummy data (`campaign_agent.skills.servers.commercial:margin_after_funding`). Target binding: Commercial mart margin view + shelf price API.
