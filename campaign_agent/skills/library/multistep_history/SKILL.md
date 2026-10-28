# multistep_history

Block 9. Past Multi-Step participation, completion and incremental spend by cohort on this brand and category.

## When to use
For audience selection and the Customer advocate's case.

## When not to use
For forecasting a new campaign - that is the external draft process.

## Inputs
- `brand_id` (str)
- `cohort` (str?)

## Outputs
- `brand_id` (str)
- `campaigns_in_category` (int)
- `by_cohort` (dict)

## Caveats
Averages across category campaigns; campaigns_on_brand says how many were on this brand.

Sources: campaign_mart - freshness: On campaign close.
Binding today: dummy data (`campaign_agent.skills.servers.ops:multistep_history`). Target binding: Campaign mart + causal uplift outputs (feat_multistep_outcome).
