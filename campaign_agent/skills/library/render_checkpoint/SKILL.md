# render_checkpoint

Renders a checkpoint card: summary, key numbers and the Approve / Ask / Edit / Send back actions.

## When to use
At the end of every stage.

## When not to use
-

## Inputs
- `stage` (str)
- `title` (str)
- `summary` (list)
- `key_numbers` (list?)
- `actions` (list?)
- `compact` (bool?)

## Outputs
- `text` (str)

## Caveats
-

Sources: none (pure function) - freshness: -.
Binding today: dummy data (`campaign_agent.skills.servers.chat:render_checkpoint`). Target binding: Teams adaptive card / web chat card.
