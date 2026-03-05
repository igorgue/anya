# Plan: GitHub Copilot Provider for Anya

## Background: How the Auth Works

Two-stage OAuth device flow, no web server needed.

**Stage 1 — Device OAuth (get a GitHub OAuth token):**
1. POST to `https://github.com/login/device/code` with a `client_id`
2. GitHub responds with a `user_code`, `verification_uri`, `device_code`, and polling `interval`
3. User visits `https://github.com/login/device` and enters the code
4. Poll `https://github.com/login/oauth/access_token` until we get an `access_token` (long-lived, starts with `gho_`)
5. Store this token on disk at `~/.local/share/anya/copilot_token`

**Stage 2 — Token Exchange (GitHub token → short-lived Copilot API token):**
1. GET `https://api.github.com/copilot_internal/v2/token` with `Authorization: Bearer <github_token>` plus required headers
2. Response contains a short-lived Copilot API token (`expires_at` ~30 min) and `endpoints.api`
3. Cache this token in memory, refresh when within ~5 minutes of expiry

**Making API calls:**
Use the Copilot token as `Authorization: Bearer <copilot_token>` to hit the chat completions endpoint
at `https://api.githubcopilot.com/chat/completions` (OpenAI chat completions compatible).

---

## Files to Create / Modify

| File | Action |
|------|--------|
| `rplugin/python3/anya/copilot_auth.py` | **New** — device flow + token exchange + caching |
| `rplugin/python3/anya/copilot_model.py` | **New** — ModelProvider using copilot token |
| `rplugin/python3/anya/model_provider.py` | **Extend** — add copilot detection + dispatch |
| `rplugin/python3/anya/agents/__init__.py` | **Minor** — recognize `"copilot"` as valid api_type |
| `rplugin/python3/anya/plugin.py` | **Add** `:Anya copilot login/logout/status` command |
| `AGENTS.md` | **Document** new env vars |

---

## Implementation Details

### 1. `copilot_auth.py` — New File

```python
class CopilotAuth:
    CLIENT_ID = "Iv1.b507a08c87ecfe98"
    # Token storage
    TOKEN_PATH = ~/.local/share/anya/copilot_token       # long-lived github token
    API_KEY_CACHE = ~/.local/share/anya/copilot_api_key.json  # short-lived copilot token

    async def device_flow() -> str
        # POST github.com/login/device/code -> get user_code + verification_uri
        # Print instructions to user: "Visit <url> and enter code <code>"
        # Poll login/oauth/access_token until access_token received
        # Save github token to TOKEN_PATH
        # Return github token

    async def get_github_token() -> str | None
        # Read from TOKEN_PATH, return None if missing

    async def get_copilot_token() -> str
        # Check in-memory cache first
        # If expired or missing, call exchange_token()
        # Return short-lived copilot token

    async def exchange_token(github_token: str) -> dict
        # GET https://api.github.com/copilot_internal/v2/token
        # Headers: Authorization, Editor-Version, Copilot-Integration-Id, etc.
        # Returns {token, expires_at, endpoints: {api: ...}}
        # Saves result to API_KEY_CACHE

    def is_logged_in() -> bool
        # Returns True if TOKEN_PATH exists and is non-empty

    def get_api_base() -> str
        # Returns cached endpoints.api or "https://api.githubcopilot.com"

    def logout()
        # Deletes TOKEN_PATH and API_KEY_CACHE
```

### 2. `copilot_model.py` — New File

```python
async def get_copilot_model_provider(settings) -> ModelProvider:
    # Creates AsyncOpenAI client with:
    #   base_url = copilot_auth.get_api_base()
    #   api_key = await copilot_auth.get_copilot_token()  (refreshed on each call)
    #   default_headers = {
    #     "Editor-Version": "Neovim/0.12",
    #     "Editor-Plugin-Version": "anya/0.0.1",
    #     "Copilot-Integration-Id": "copilot-chat",
    #     "Openai-Intent": "conversation-edits",
    #   }
    # Returns ModelProvider using OpenAIChatCompletionsModel
    # Note: copilot only supports chat_completions, not responses API
```

### 3. `model_provider.py` — Extend `needs_custom_provider()`

```python
def needs_custom_provider(model, base_url=None, api_type="responses"):
    return (
        "/" in model or ":" in model
        or base_url is not None
        or api_type in ("anthropic", "copilot")  # add "copilot"
        or model.startswith("github-copilot/")
    )
```

Add branch in `get_custom_model_provider()`:
```python
if api_type == "copilot":
    from .copilot_model import get_copilot_model_provider
    return asyncio.get_event_loop().run_until_complete(get_copilot_model_provider(settings))
```

### 4. `agents/__init__.py` — Minor

Add `"copilot"` to recognized api_type values:
```python
if api_type not in {"chat_completions", "responses", "anthropic", "copilot"}:
    api_type = "responses"
```

Force `chat_completions` style for copilot (since copilot doesn't support responses API):
```python
if api_type == "copilot":
    # Copilot uses OpenAI chat completions format
    # model_settings handled via custom provider
```

### 5. `plugin.py` — Add `:Anya copilot` Subcommand

Hook into the existing `Anya` command dispatch:
```
:Anya copilot login   → run device flow, show code + URL in output, poll until done
:Anya copilot logout  → delete stored tokens
:Anya copilot status  → show: logged in/out, token expiry, api endpoint
```

The `login` flow:
1. Start async task
2. Call `copilot_auth.device_flow()` which initiates the request
3. `nvim.out_write()` with: "Visit https://github.com/login/device and enter: XXXX-XXXX"
4. Poll in background, notify when complete: "Anya: Copilot login successful!"

---

## Usage After Implementation

```bash
# 1. Login once (runs device flow)
:Anya copilot login
# → "Visit https://github.com/login/device and enter: ABCD-1234"
# → (you authorize in browser)
# → "Anya: Copilot login successful!"

# 2. Set env vars to use copilot
export ANYA_API_TYPE=copilot
export ANYA_MODEL=gpt-4o    # or claude-3.5-sonnet, o3-mini, etc.
```

Or in `init.lua`:
```lua
vim.env.ANYA_API_TYPE = "copilot"
vim.env.ANYA_MODEL = "gpt-4o"
```

## Available Copilot Models

- `gpt-4o`, `gpt-4.1`, `gpt-4.1-mini`
- `claude-3.5-sonnet`, `claude-3.7-sonnet`  
- `o3-mini`, `o1`
- (and more depending on your subscription tier)
