# brand_shoppers

Block 1. Distinct Clubcard households with at least one brand purchase in the rolling 6 weeks, vs prior 6 weeks and last year; penetration, lapsed pool and headroom.

## When to use
Always - it is a core ask of the Diagnostic Pack. Use cut=lapsed_recency when the brand is losing share or the objective is reactivation.

## When not to use
For spend per customer (use brand_spc) or cohort shares (use cohort_split).

## Inputs
- `brand_id` (str)
- `cut` (str?)

## Outputs
- `brand_id` (str)
- `shoppers_6w` (int)
- `shoppers_prior_6w` (int)
- `penetration_pct` (float)
- `lapsed_pool` (int)
- `headroom_households` (int)
- `sales_6w_brand_week` (float)
- `lapsed_by_recency` (dict?)

## Caveats
Penetration = brand shoppers / category shoppers in the same 6 weeks. Lapsed = bought in the prior 26 weeks but not in the window.

Sources: feat_customer_brand_6w, feat_brand_week - freshness: T-1.
Binding today: dummy data (`campaign_agent.skills.servers.transactions:brand_shoppers`). Target binding: Databricks SQL over feat_customer_brand_6w (customer x brand, rolling 6 wks).
