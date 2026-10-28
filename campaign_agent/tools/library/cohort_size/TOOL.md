# cohort_size

Households in one audience cohort (lapsed, light, medium, heavy, category non-buyers), optionally by lifestage.

## When to use
When the Audience Agent sizes candidate cohorts.

## When not to use
For shares of shoppers or sales (use cohort_split).

## Inputs
- `brand_id` (str)
- `cohort` (str)
- `by` (str?)

## Outputs
- `brand_id` (str)
- `cohort` (str)
- `size` (int?)
- `suppressed` (bool)

## Caveats
Returns size=null when below the minimum cell size.

Sources: feat_customer_brand_6w, segmentation_weekly - freshness: T-1.
Binding today: dummy data (`campaign_agent.tools.servers.transactions:cohort_size`). Target binding: Databricks SQL count over feat_customer_brand_6w.
