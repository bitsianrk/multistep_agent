# supplier_funding_lookup

Block 8. Supplier funding rates by PSG and an indicative funding pool for the brand.

## When to use
When a discussion needs to know whether the supplier funds the brand.

## When not to use
For reward or coupon funding decisions - out of scope; those belong to the external draft process.

## Inputs
- `brand_id` (str)

## Outputs
- `brand_id` (str)
- `supplier` (str)
- `funding_by_psg_pct` (dict)
- `est_funding_pool_6w_gbp` (float)

## Caveats
Funding is indicative; funding_confirmed is always false here.

Sources: commercial_mart - freshness: Daily.
Binding today: dummy data (`campaign_agent.skills.servers.commercial:supplier_funding_lookup`). Target binding: Commercial mart supplier funding table.
