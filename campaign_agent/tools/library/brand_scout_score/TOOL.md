# brand_scout_score

Ranks other brands in the requested brand's category on seasonal fit, market momentum, sentiment, headroom and margin for the campaign window. Use when deciding whether to keep or switch brand.

## When to use
On every request; it proposes alternatives only when one beats the requested brand on seasonal and market signals.

## When not to use
To pick PSGs or audiences.

## Inputs
- `brand_id` (str)
- `window_start` (date)
- `window_weeks` (int)
- `switch_bias` (float?)

## Outputs
- `brand_id` (str)
- `requested` (dict)
- `alternatives` (list)

## Caveats
beats_requested needs a score lead of scout_beat_margin and a better seasonal fit or momentum.

Sources: feat_brand_week, kantar_cache, sentiment_daily, commercial_mart - freshness: T-1 (Kantar per release period).
Binding today: dummy data (`campaign_agent.tools.servers.market:brand_scout_score`). Target binding: sql/brand_scout.sql + kantar_share.
