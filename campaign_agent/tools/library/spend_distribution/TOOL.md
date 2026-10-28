# spend_distribution

Block 4. Percentiles of household brand spend and median inter-purchase interval, by cohort.

## When to use
To size step-free spend bands and see how concentrated spend is; with cohort=lapsed for pre-lapse spend.

## When not to use
For averages (use brand_spc).

## Inputs
- `brand_id` (str)
- `cohort` (str?)

## Outputs
- `brand_id` (str)
- `by_cohort` (dict)

## Caveats
Inter-purchase interval is approximated as 42 days / trips for households with 2+ trips.

Sources: feat_customer_brand_6w - freshness: T-1.
Binding today: dummy data (`campaign_agent.tools.servers.transactions:spend_distribution`). Target binding: Databricks SQL percentile_approx over feat_customer_brand_6w.
