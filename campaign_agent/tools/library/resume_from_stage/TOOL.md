# resume_from_stage

Validates a send-back target and says which run state must be cleared to resume there.

## When to use
When a human sends the run back to an earlier stage.

## When not to use
-

## Inputs
- `brand_id` (str)
- `stage` (str)

## Outputs
- `stage` (str)
- `clears` (list)

## Caveats
-

Sources: none (pure function) - freshness: -.
Binding today: dummy data (`campaign_agent.tools.servers.planning:resume_from_stage`). Target binding: Orchestrator state store.
