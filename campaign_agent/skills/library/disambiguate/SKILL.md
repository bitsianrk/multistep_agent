# disambiguate

Maps a human's clarification reply onto exactly one of the candidate brands.

## When to use
When ner_extract returned more than one brand.

## When not to use
-

## Inputs
- `text` (str)
- `candidates` (list)

## Outputs
- `brand_id` (str?)
- `confidence` (float)

## Caveats
Returns brand_id=null when the reply does not settle it - ask again.

Sources: taxonomy - freshness: On range change.
Binding today: dummy data (`campaign_agent.skills.servers.taxonomy:disambiguate`). Target binding: Taxonomy graph.
