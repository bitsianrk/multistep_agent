# brand_spc

Block 2. Spend per customer = brand sales / brand shoppers = frequency x spend per trip; average basket value of brand-containing trips. Optional cohort filter.

## When to use
Always (core ask). Call again with cohort=<tier> when a human asks for SPC of one cohort.

## When not to use
For distribution shape (use spend_distribution).

## Inputs
- `brand_id` (str)
- `cohort` (str?)

## Outputs
- `brand_id` (str)
- `cohort` (str)
- `suppressed` (bool)

## Caveats
For cohort=lapsed, window SPC is zero by definition, so the prior-6-week SPC is returned instead.

Sources: feat_customer_brand_6w - freshness: T-1.
Binding today: dummy data (`campaign_agent.skills.servers.transactions:brand_spc`). Target binding: Databricks SQL over feat_customer_brand_6w.
