# audience_propose

Scores candidate cohorts for the objective using sizes and past Multi-Step response, and proposes which to include with rationale.

## When to use
After cohorts are sized, at the end of Diagnose.

## When not to use
-

## Inputs
- `brand_id` (str)
- `objective` (str)
- `cohort_sizes` (dict)
- `response` (dict)
- `cohort_bias` (dict?)

## Outputs
- `brand_id` (str)
- `cohorts` (list)
- `total_included` (int)

## Caveats
cohort_bias carries learned adjustments from human overrides.

Sources: feat_customer_brand_6w, campaign_mart - freshness: T-1.
Binding today: dummy data (`campaign_agent.skills.servers.planning:audience_propose`). Target binding: Azure ML audience model endpoint.
