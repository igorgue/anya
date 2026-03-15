# Plan: add `@conversation_id` references in prompt completion and LLM context

## Goal
Support `@conversation_id` mentions alongside `@file` mentions so you can:
- type `@s3j28f3j`
- see the conversation title in the blink completion menu
- select the old conversation from completion
- have the LLM receive the referenced conversation as explicit context, not as a plain string

## What exists today
- `lua/anya/blink/files.lua` only completes project files after `@`.
- `ftplugin/anya-prompt.lua` highlights any `@token` as a file reference.
- conversations already have `id`, `title`, `cwd`, timestamps, and message history in SQLite.
- the picker already loads conversation titles via `AnyaListConversations()`.
- the send pipeline builds `llm_history` in `rplugin/python3/anya/plugin.py` before sending the request to the daemon.

## Proposed implementation

### 1. Make `@` completion a mixed source: files + conversations
Update `lua/anya/blink/files.lua` so the `@` source returns both:
- file matches
- conversation matches

For conversation completion items:
- `label`: conversation title (fallback: `Untitled conversation`)
- `detail`: conversation ID and maybe updated date/cwd
- `insertText`: the raw conversation ID only, so prompt text stays `@s3j28f3j`
- `kind`: a distinct LSP kind if possible, otherwise text/reference-ish
- sorting: exact ID matches first, then title fuzzy matches, then recency

This preserves the current user-facing syntax while making the completion list human-readable.

### 2. Add a lightweight RPC/search API for conversation mentions
Add a sync function on the Python side for mention completion, something like:
- `AnyaSearchConversationMentions(query, limit)`

Back it with a DB helper that searches:
- exact/partial `id`
- title text
- ordered by exactness + recency

Return minimal metadata needed by blink:
- `id`
- `title`
- `updated_at`
- `cwd`

### 3. Resolve `@...` references before sending to the daemon
In the send pipeline (`rplugin/python3/anya/plugin.py`), add a preprocessing step for the latest user message.

Behavior:
- scan the last user message for `@token`
- if `token` matches a real conversation ID, treat it as a conversation reference
- otherwise leave existing file behavior alone

For each matched conversation mention, append a structured block to the final user message, for example:

```text
Referenced conversations:
- @s3j28f3j — Fix daemon startup race
  Conversation ID: s3j28f3j
  Created in: /path/to/project
  Transcript:
  [trimmed conversation content here]
```

Important: do not rely on the model to infer that `@s3j28f3j` is special; explicitly inject the referenced conversation content.

### 4. Keep the referenced conversation payload bounded
To avoid exploding context size, load a bounded representation of the referenced conversation:
- preferred: full rebuilt conversation content if short enough
- otherwise: most recent N messages or a character/token budget
- strip UI-only markers before injection

A good first version is:
- rebuild conversation content from DB
- strip markers
- cap to a configured size per referenced conversation

Later this can evolve into “prefer compacted summary if available”.

### 5. Make prompt highlighting conversation-aware
Adjust `ftplugin/anya-prompt.lua` so `@conversation_id` uses the same highlight path as file refs, or split into two highlight groups later.

At minimum:
- keep highlighting generic `@token` references
- avoid naming/comments that imply only files

### 6. Decide collision rules explicitly
Because `@foo` may be either a file or a conversation-like token, define clear precedence:
- completion UI can show both groups
- send-time resolution should prefer conversation only on exact conversation ID match
- otherwise it remains just text / file mention

That avoids breaking existing `@filename.py` behavior.

## Suggested file changes
- `lua/anya/blink/files.lua`
  - expand source from files-only to mixed references
- `ftplugin/anya-prompt.lua`
  - rename/generalize `@filepath` comments/highlights if needed
- `rplugin/python3/anya/db.py`
  - add conversation mention search helper
- `rplugin/python3/anya/plugin.py`
  - add RPC for mention search
  - preprocess latest user message to expand referenced conversations before daemon send
- possibly add a tiny helper module for mention parsing/expansion if `plugin.py` gets too large

## Testing plan
1. Completion
   - typing `@s3` shows conversations by title with IDs/details
   - typing `@lua/anya` still shows file matches
2. Insertion
   - selecting a conversation inserts only its ID after `@`
3. Send behavior
   - sending a prompt with `@conversation_id` injects referenced conversation context into the final LLM input
4. Non-regression
   - plain file mentions still work
   - unknown `@token` does not crash or inject junk
5. Collision
   - if a file-like token resembles an ID, only exact conversation ID match is treated as a conversation reference

## Nice follow-ups
- render conversation items with a custom icon in blink
- add support for `@title words` search, not just IDs
- support cross-conversation summaries instead of raw transcripts
- show a hover/preview for the selected conversation mention
