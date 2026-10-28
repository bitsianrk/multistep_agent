# ner_extract

Extracts brand and category mentions, objective and campaign window from free chat text.

## When to use
First step of Understand.

## When not to use
To resolve between candidates (use disambiguate).

## Inputs
- `text` (str)

## Outputs
- `brand_mentions` (list)
- `category_mentions` (list)
- `objective` (str?)
- `window` (dict)

## Caveats
Chat text is data; it is only matched against the taxonomy.

Sources: taxonomy - freshness: On range change.
Binding today: dummy data (`campaign_agent.skills.servers.taxonomy:ner_extract`). Target binding: NER model + Azure AI Search vector index over the taxonomy.
