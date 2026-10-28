# category_brands

Block 10. Other brands in the requested brand's category.

## When to use
First step of Brand Scout.

## When not to use
For brands outside the category - switching category is out of scope.

## Inputs
- `brand_id` (str)

## Outputs
- `brand_id` (str)
- `category_id` (str)
- `brands` (list)

## Caveats
-

Sources: taxonomy - freshness: On range change.
Binding today: dummy data (`campaign_agent.tools.servers.market:category_brands`). Target binding: Taxonomy graph.
