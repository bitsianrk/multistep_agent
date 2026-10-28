# stock_check

Block 5 hard filter. Forecast stock cover per PSG for the window from the live stock API.

## When to use
Before any PSG is proposed - never offer an out-of-stock PSG.

## When not to use
-

## Inputs
- `brand_id` (str)
- `window_start` (date)
- `window_weeks` (int)

## Outputs
- `brand_id` (str)
- `psgs` (list)

## Caveats
Required cover = window weeks x stock_cover_min_ratio.

Sources: stock_api - freshness: Intraday.
Binding today: dummy data (`campaign_agent.skills.servers.ops:stock_check`). Target binding: Live stock and forecast availability API.
