---
name: entity-resolution
description: Map the free-text campaign request to exactly one brand, an objective and a campaign window; ask back
  when ambiguous.
agent: entity_resolution
tools:
- ner_extract
- taxonomy_lookup
- disambiguate
- get_run_state
- recall_memory
- remember
reasoning_effort: medium
max_iterations: 6
max_tokens: 1500
warmup:
- ner_extract
output_schema:
  type: object
  required:
  - brand_id
  - candidates
  - objective
  - window
  - confidence
  properties:
    brand_id:
      type:
      - string
      - 'null'
    candidates:
      type: array
      items:
        type: string
    objective:
      type:
      - string
      - 'null'
      enum:
      - reactivate
      - grow
      - retain
      - trial
      - null
    window:
      type: object
      required:
      - start
      - weeks
      - assumed
      properties:
        start:
          type: string
        weeks:
          type: integer
          minimum: 1
          maximum: 26
        assumed:
          type: array
          items:
            enum:
            - start
            - weeks
    confidence:
      type: number
      minimum: 0
      maximum: 1
---

# Entity Resolution

Turn the campaign manager's request (or their reply to a clarification) into exactly one brand ID, an objective and a window.

## Procedure
1. Call `ner_extract` on the text. It matches against the product taxonomy and parses the objective and window.
2. If several brands match, check whether the text names a category ("crisps", "coffee") that settles it. Call `disambiguate` with the reply and the candidates when answering a clarification.
3. Confirm the chosen brand with `taxonomy_lookup`.
4. Call `finish`:
   - `brand_id`: the single brand, or null if it is still ambiguous (then list `candidates`).
   - `objective`: reactivate (lapsed / win back), grow (frequency / spend), retain (loyal / defend), trial (new / non-buyers), or null if not stated.
   - `window`: from `ner_extract`. Keep its `assumed` list so the human sees the defaults.
   - `confidence`: 1.0 for an exact brand name, lower for fuzzy matches, 0 when unresolved.

## Rules
- Never guess between two brands. Return null with candidates so the human is asked.
- Never invent an objective. Null means the human will be asked.
- If the task contains a pending resolution, keep what is already resolved and only fill the gaps from the reply.
