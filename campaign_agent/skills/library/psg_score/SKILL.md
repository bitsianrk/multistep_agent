# psg_score

Block 5. Reach (% brand shoppers buying the PSG) and momentum (6 wks vs prior 6 wks and YoY) per PSG, combined into a hero score.

## When to use
When choosing hero PSGs inside the brand. The default qualifying basket is the whole brand.

## When not to use
For hard filters (use stock_check, promo_clash_check, margin_after_funding).

## Inputs
- `brand_id` (str)
- `hero_bias` (dict?)

## Outputs
- `brand_id` (str)
- `psgs` (list)

## Caveats
hero_bias carries learned adjustments from past human overrides; keep it small.

Sources: feat_customer_brand_6w, feat_psg_week - freshness: T-1.
Binding today: dummy data (`campaign_agent.skills.servers.transactions:psg_score`). Target binding: Databricks SQL over feat_psg_week + feat_customer_brand_6w.
