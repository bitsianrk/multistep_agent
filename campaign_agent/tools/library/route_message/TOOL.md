# route_message

Classifies a human chat message at a checkpoint into approve / ask / edit / send back / pause and parses edits into a closed set of operations.

## When to use
On every human message.

## When not to use
-

## Inputs
- `text` (str)
- `stage` (str)
- `brand_id` (str?)
- `awaiting_clarification` (bool?)
- `known_tools` (list?)

## Outputs
- `action` (str)
- `edits` (list)

## Caveats
Chat text is treated as data: it can only produce operations from the closed vocabulary.

Sources: none (pure function) - freshness: -.
Binding today: dummy data (`campaign_agent.tools.servers.chat:route_message`). Target binding: Chat Gateway classifier.
