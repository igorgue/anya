# Token Counting Summary

This project implements a detailed system for counting and managing tokens within its context, especially in relation to model and session constraints. The token counting logic supports robust session management and helps prevent context overflows. Below are the key aspects of how token usage and limits are handled:

## Main Concepts

### 1. Token Usage Tracking
- Tokens are tracked with the following main types:
  - `input`: Tokens sent as input to the model
  - `output`: Tokens returned as output from the model
  - `reasoning`: (Optional) Tokens attributed to model reasoning
  - `cache.read`: Tokens read from cache (i.e., reused past responses)
  - `cache.write`: Tokens written to cache

### 2. Estimating Token Count
- There is a utility to estimate the number of tokens based on text length.
  - Test examples show a heuristic of roughly 4 characters per token ("xxxx" = 1 token).
  - `Token.estimate(text)` returns the estimated token count for a given string.

### 3. Session Usage Aggregation
- Session usage aggregates token counts from actual model responses (`inputTokens`, `outputTokens`) and also accounts for cached and reasoning tokens where available.
- Cached tokens (`cachedInputTokens`) are handled specially to subtract or track separate from standard input tokens. Behavior varies for different providers (e.g., Anthropic).

### 4. Overflow Detection
- The function `SessionCompaction.isOverflow` checks if the current token usage exceeds the model's usable context limit:
  - Inputs: token counts for input, output, (optionally cache.read), and the model's defined context size
  - If the total exceeds the context window, compaction/overflow logic is triggered
  - Edge cases are handled:
    - If model context limit is 0, overflow is always false
    - Compaction can be disabled via config to bypass

### 5. Cost Calculation
- Token usage is also linked to cost calculation, supporting cost per token for input, output, and cache operations. Costs are summed as needed.

## Example Logic Summarized (from tests)
- To estimate overflow, add up `input`, `output`, and `cache.read` tokens and compare against model context limit.
- Adjust for cached token behaviors, especially with specific providers.
- Use `Token.estimate` to predict usage from arbitrary text, based on an average chars-to-token ratio of 4:1.

---

This summary is based on the test coverage and exposed interfaces around token usage and compaction for context windows. For full implementation details, review the underlying code in `SessionCompaction`, `Token`, and `Session` modules.
