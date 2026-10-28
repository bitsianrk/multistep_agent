# cohort_split

Block 3. % shoppers and % sales by loyalty tier (lapsed / light / medium / heavy), lifestage, value segment and Clubcard Plus.

## When to use
Always (core ask); it feeds audience selection.

## When not to use
For cohort sizes used in the audience proposal (use cohort_size).

## Inputs
- `brand_id` (str)

## Outputs
- `brand_id` (str)
- `loyalty_tier` (list)
- `lifestage` (list)
- `value_segment` (list)
- `clubcard_plus` (list)

## Caveats
Cells below the minimum cell size are suppressed. Loyalty tier shares are over the brand base (active + lapsed).

Sources: feat_customer_brand_6w, segmentation_weekly - freshness: T-1 (segments weekly).
Binding today: dummy data (`campaign_agent.tools.servers.transactions:cohort_split`). Target binding: Databricks SQL: feat_customer_brand_6w joined to the weekly segmentation snapshot.
