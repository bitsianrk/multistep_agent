# promo_clash_check

Block 5 hard filter. Live promotions (Clubcard Prices, coupons) that overlap the campaign window.

## When to use
Before any PSG is proposed - avoids double discounting.

## When not to use
-

## Inputs
- `brand_id` (str)
- `window_start` (date)
- `window_weeks` (int)

## Outputs
- `brand_id` (str)
- `clashes` (list)

## Caveats
-

Sources: promotions_api - freshness: Near real time.
Binding today: dummy data (`campaign_agent.tools.servers.ops:promo_clash_check`). Target binding: Live promotions calendar API / event stream.
