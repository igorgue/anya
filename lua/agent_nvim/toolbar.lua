-- Toolbar for agent.nvim showing agent and mode selection
-- Uses a floating window positioned at bottom-right of prompt window
local M = {}

-- Main agent options
local MAIN_AGENTS = { "AUTO", "CODER", "PLAN" }
-- All available agents
local ALL_AGENTS = { "AUTO", "CODER", "REVIEWER", "VERIFIER", "PLAN", "COMPACT" }
-- Mode options
local MODES = { "ASK", "YOLO" }

-- Highlight groups
local HIGHLIGHTS = {
  AUTO = "Comment",
  ASK = "Comment",
  CODER = "DiagnosticWarn",
  YOLO = "DiagnosticError",
  REVIEWER = "DiagnosticInfo",
  VERIFIER = "DiagnosticHint",
  PLAN = "Special",
  COMPACT = "Identifier",
}

-- State
local state = {
  agent = "AUTO",
  mode = "ASK",
  toolbar_win = nil,
  toolbar_buf = nil,
  prompt_win = nil,
  prompt_buf = nil,
}

-- Load state from global Lua variable (will be set by Python)
local function load_state_from_python()
  if _G.agent_state then
    state.agent = _G.agent_state.agent or "AUTO"
    state.mode = _G.agent_state.mode or "ASK"
  end
end

-- Initialize state on load
load_state_from_python()

--- Close existing toolbar window if it exists
local function close_toolbar_win()
  if state.toolbar_win and vim.api.nvim_win_is_valid(state.toolbar_win) then
    vim.api.nvim_win_close(state.toolbar_win, true)
  end
  state.toolbar_win = nil
end

--- Update the toolbar display using a floating window
local function update_toolbar()
  -- Check if we have a valid prompt buffer
  if not state.prompt_buf or not vim.api.nvim_buf_is_valid(state.prompt_buf) then
    close_toolbar_win()
    return
  end

  -- Find the window displaying the prompt buffer
  local prompt_win = nil
  for _, win in ipairs(vim.api.nvim_list_wins()) do
    if vim.api.nvim_win_get_buf(win) == state.prompt_buf then
      prompt_win = win
      break
    end
  end

  if not prompt_win or not vim.api.nvim_win_is_valid(prompt_win) then
    close_toolbar_win()
    return
  end

  state.prompt_win = prompt_win

  -- Build toolbar text
  local toolbar_text = state.agent .. " | " .. state.mode
  local width = #toolbar_text

  -- Get prompt window dimensions
  local win_width = vim.api.nvim_win_get_width(prompt_win)
  local win_height = vim.api.nvim_win_get_height(prompt_win)

  -- Create or reuse toolbar buffer
  if not state.toolbar_buf or not vim.api.nvim_buf_is_valid(state.toolbar_buf) then
    state.toolbar_buf = vim.api.nvim_create_buf(false, true)
    vim.api.nvim_set_option_value("buftype", "nofile", { buf = state.toolbar_buf })
    vim.api.nvim_set_option_value("bufhidden", "hide", { buf = state.toolbar_buf })
  end

  -- Safety check - ensure buffer is still valid after creation
  if not vim.api.nvim_buf_is_valid(state.toolbar_buf) then
    return
  end

  -- Set toolbar content
  vim.api.nvim_buf_set_lines(state.toolbar_buf, 0, -1, false, { toolbar_text })

  -- Apply highlights to the toolbar buffer (using bold variants)
  local agent_hl = "AgentToolbar" .. state.agent
  local mode_hl = "AgentToolbar" .. state.mode
  local agent_len = #state.agent
  local separator_start = agent_len
  local mode_start = agent_len + 3  -- " | " is 3 chars

  vim.api.nvim_buf_add_highlight(state.toolbar_buf, -1, agent_hl, 0, 0, agent_len)
  vim.api.nvim_buf_add_highlight(state.toolbar_buf, -1, "AgentToolbarSeparator", 0, separator_start, mode_start)
  vim.api.nvim_buf_add_highlight(state.toolbar_buf, -1, mode_hl, 0, mode_start, -1)

  -- Calculate position: bottom-left of prompt window
  local row = win_height - 1  -- Bottom row (0-indexed, relative to window)
  local col = 0  -- Left-aligned

  -- Close existing window before creating new one
  close_toolbar_win()

  -- Create floating window
  state.toolbar_win = vim.api.nvim_open_win(state.toolbar_buf, false, {
    relative = "win",
    win = prompt_win,
    width = width,
    height = 1,
    row = row,
    col = col,
    style = "minimal",
    focusable = false,
    zindex = 50,
  })

  -- Make toolbar window transparent and non-interactive
  if state.toolbar_win and vim.api.nvim_win_is_valid(state.toolbar_win) then
    vim.api.nvim_set_option_value("winhl", "Normal:AgentToolbarBg,NormalFloat:AgentToolbarBg", { win = state.toolbar_win })
  end
end

-- Create highlight groups for toolbar (bold + transparent)
local function setup_highlights()
  -- Create transparent background group
  vim.api.nvim_set_hl(0, "AgentToolbarBg", { bg = "NONE" })
  
  -- Create separator highlight (Comment, non-bold)
  local comment_hl = vim.api.nvim_get_hl(0, { name = "Comment", link = false })
  vim.api.nvim_set_hl(0, "AgentToolbarSeparator", {
    fg = comment_hl.fg,
    bg = "NONE",
  })
  
  -- Create highlight variants (bold for non-Comment, non-bold for Comment)
  for name, base_hl in pairs(HIGHLIGHTS) do
    local hl_info = vim.api.nvim_get_hl(0, { name = base_hl, link = false })
    local is_comment = base_hl == "Comment"
    vim.api.nvim_set_hl(0, "AgentToolbar" .. name, {
      fg = hl_info.fg,
      bg = "NONE",
      bold = not is_comment,
    })
  end
end

-- Setup highlights on load
setup_highlights()

-- Re-setup highlights when colorscheme changes
vim.api.nvim_create_autocmd("ColorScheme", {
  callback = setup_highlights,
})

--- Cycle through main agents
function M.toggle_agent()
  local current_idx = 1
  for i, agent in ipairs(MAIN_AGENTS) do
    if agent == state.agent then
      current_idx = i
      break
    end
  end

  local next_idx = (current_idx % #MAIN_AGENTS) + 1
  state.agent = MAIN_AGENTS[next_idx]

  -- Notify Python of the change
  if _G.agent_config_callback then
    _G.agent_config_callback("agent", state.agent)
  end

  update_toolbar()
end

--- Toggle between modes
function M.toggle_mode()
  if state.mode == "ASK" then
    state.mode = "YOLO"
  else
    state.mode = "ASK"
  end

  -- Notify Python of the change
  if _G.agent_config_callback then
    _G.agent_config_callback("mode", state.mode)
  end

  update_toolbar()
end

--- Open picker for specialized agents
function M.pick_agent()
  vim.ui.select(ALL_AGENTS, {
    prompt = "Select Agent: ",
  }, function(choice)
    if choice then
      state.agent = choice
      if _G.agent_config_callback then
        _G.agent_config_callback("agent", state.agent)
      end
      update_toolbar()
    end
  end)
end

--- Set agent and mode from Python configuration
function M.set_state(agent, mode)
  state.agent = agent or state.agent
  state.mode = mode or state.mode
  update_toolbar()
end

--- Get current state
function M.get_state()
  return {
    agent = state.agent,
    mode = state.mode,
  }
end

--- Initialize toolbar for a buffer
function M.init_buffer(bufnr)
  state.prompt_buf = bufnr

  -- Set up autocmd to update toolbar on various events
  local group = vim.api.nvim_create_augroup("AgentToolbar_" .. bufnr, { clear = true })

  vim.api.nvim_create_autocmd({ "WinResized", "VimResized" }, {
    group = group,
    callback = function()
      if vim.api.nvim_buf_is_valid(bufnr) then
        vim.schedule(update_toolbar)
      end
    end,
  })

  vim.api.nvim_create_autocmd("BufWinEnter", {
    group = group,
    buffer = bufnr,
    callback = function()
      vim.schedule(update_toolbar)
    end,
  })

  vim.api.nvim_create_autocmd("BufWipeout", {
    group = group,
    buffer = bufnr,
    callback = function()
      M.cleanup()
    end,
  })

  -- Update toolbar initially (defer to ensure window exists)
  vim.schedule(update_toolbar)
end

--- Cleanup toolbar
function M.cleanup()
  close_toolbar_win()
  if state.toolbar_buf and vim.api.nvim_buf_is_valid(state.toolbar_buf) then
    pcall(vim.api.nvim_buf_delete, state.toolbar_buf, { force = true })
  end
  state.toolbar_buf = nil
  state.prompt_buf = nil
  state.prompt_win = nil
end

return M
