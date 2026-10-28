# taxonomy_lookup

Brand details from the product knowledge graph: category, supplier and PSGs.

## When to use
After a brand is resolved.

## When not to use
-

## Inputs
- `brand_id` (str)

## Outputs
- `brand_id` (str)
- `name` (str)
- `category_id` (str)
- `psgs` (list)

## Caveats
-

Sources: taxonomy - freshness: On range change.
Binding today: dummy data (`campaign_agent.skills.servers.taxonomy:taxonomy_lookup`). Target binding: Taxonomy graph.
