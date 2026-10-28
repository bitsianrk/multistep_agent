# seasonality_index

Block 6. Seasonality index for the window = expected weekly brand sales in the window / 52-week mean, from 156 weeks of history; optionally per PSG.

## When to use
Always for the brand; with level=psg when the window covers peak weeks.

## When not to use
For the trend of the brand (use kantar_share).

## Inputs
- `brand_id` (str)
- `window_start` (date)
- `window_weeks` (int)
- `level` (str?)

## Outputs
- `brand_id` (str)
- `seasonality_index` (float)

## Caveats
Index >1 means the window is seasonally stronger than an average week.

Sources: feat_brand_week, feat_psg_week, seasonality_monthly - freshness: Monthly.
Binding today: dummy data (`campaign_agent.tools.servers.ops:seasonality_index`). Target binding: Precomputed seasonality table per PSG and ISO week.
