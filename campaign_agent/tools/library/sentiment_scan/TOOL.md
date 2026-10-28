# sentiment_scan

Block 7. Net sentiment for the last 30 days vs the prior 30 days from reviews, social and search trends.

## When to use
For the sentiment driver in Brand Scout and the Customer advocate's case.

## When not to use
As a sales measure - sentiment is directional only.

## Inputs
- `brand_id` (str)

## Outputs
- `brand_id` (str)
- `net_sentiment_30d` (float)
- `sentiment_trend` (float)

## Caveats
Net sentiment is on a -100..100 scale.

Sources: sentiment_daily - freshness: Daily.
Binding today: dummy data (`campaign_agent.tools.servers.market:sentiment_scan`). Target binding: Batch ingest + LLM classification table sentiment_daily.
