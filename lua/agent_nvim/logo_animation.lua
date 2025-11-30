-- lua/agent_nvim/logo_animation.lua
-- Animated logo reveal for agent.nvim welcome screen

local M = {}

-- Logo lines (ASCII art)
M.logo_lines = {
  "░█▀█░█▀▀░█▀▀░█▀█░▀█▀░░░░█▀█░█░█░▀█▀░█▄█",
  "░█▀█░█░█░█▀▀░█░█░░█░░░░░█░█░▀▄▀░░█░░█░█",
  "░▀░▀░▀▀▀░▀▀▀░▀░▀░░▀░░▀░░▀░▀░░▀░░▀▀▀░▀░▀",
}

-- Full welcome message structure
M.welcome_message = {
  "```",
  "░█▀█░█▀▀░█▀▀░█▀█░▀█▀░░░░█▀█░█░█░▀█▀░█▄█",
  "░█▀█░█░█░█▀▀░█░█░░█░░░░░█░█░▀▄▀░░█░░█░█",
  "░▀░▀░▀▀▀░▀▀▀░▀░▀░░▀░░▀░░▀░▀░░▀░░▀▀▀░▀░▀",
  "```",
  "",
  "> Type your request in the prompt below.",
}

-- Animation state
M.state = {
  timer = nil,
  bufnr = nil,
  current_col = 0,
  direction = 1, -- 1 = right, -1 = left
  animation_complete = false,
  highlight_ns = nil,
  jiggle_count = 0, -- Remaining jiggle movements
  jiggle_direction = 1, -- Current jiggle direction
  paused_at_edge = false, -- Whether we're paused at an edge
}

-- Get the maximum width of logo lines
local function get_logo_width()
  local max_width = 0
  for _, line in ipairs(M.logo_lines) do
    max_width = math.max(max_width, vim.fn.strchars(line))
  end
  return max_width
end

-- Animate the logo with a left-to-right reveal effect
function M.animate_logo(bufnr, on_complete)
  -- Stop any existing animation
  M.stop_animation()

  M.state.bufnr = bufnr
  M.state.current_col = 0
  M.state.animation_complete = false

  local logo_width = get_logo_width()
  local interval_ms = 25 -- Speed of reveal (ms per column)

  -- First, set up the buffer with masked logo (spaces)
  local initial_lines = { "```" }
  for _, line in ipairs(M.logo_lines) do
    -- Start with spaces (same width as final line)
    local masked = string.rep(" ", vim.fn.strchars(line))
    table.insert(initial_lines, masked)
  end
  table.insert(initial_lines, "```")
  table.insert(initial_lines, "")
  table.insert(initial_lines, "> Type your request in the prompt below.")

  vim.api.nvim_buf_set_lines(bufnr, 0, -1, false, initial_lines)

  -- Create timer for animation
  M.state.timer = vim.loop.new_timer()
  M.state.timer:start(
    100, -- Initial delay before animation starts
    interval_ms,
    vim.schedule_wrap(function()
      if not vim.api.nvim_buf_is_valid(bufnr) then
        M.stop_animation()
        return
      end

      M.state.current_col = M.state.current_col + 1

      -- Update each logo line to reveal up to current_col
      for i, line in ipairs(M.logo_lines) do
        local line_idx = i -- Line 1 is ```, logo starts at line 2 (index 1)
        local chars = vim.fn.split(line, [[\zs]]) -- Split into individual characters
        local revealed = {}

        for col = 1, #chars do
          if col <= M.state.current_col then
            table.insert(revealed, chars[col])
          else
            table.insert(revealed, " ")
          end
        end

        local new_line = table.concat(revealed)
        vim.api.nvim_buf_set_lines(bufnr, line_idx, line_idx + 1, false, { new_line })
      end

      -- Check if animation is complete
      if M.state.current_col >= logo_width then
        M.stop_animation()
        M.state.animation_complete = true
        if on_complete then
          on_complete()
        end
      end
    end)
  )
end

-- Animate with a cascade/waterfall effect (characters fall into place)
function M.animate_logo_cascade(bufnr, on_complete)
  -- Stop any existing animation
  M.stop_animation()

  M.state.bufnr = bufnr
  M.state.current_col = 0
  M.state.animation_complete = false

  local logo_width = get_logo_width()
  local interval_ms = 20 -- Speed of cascade

  -- Random characters for the cascade effect
  local cascade_chars = { "▀", "▄", "█", "▌", "▐", "░", "▒", "▓", "│", "─" }

  -- First, set up empty buffer
  local initial_lines = { "```" }
  for _ = 1, #M.logo_lines do
    table.insert(initial_lines, string.rep(" ", logo_width))
  end
  table.insert(initial_lines, "```")
  table.insert(initial_lines, "")
  table.insert(initial_lines, "> Type your request in the prompt below.")

  vim.api.nvim_buf_set_lines(bufnr, 0, -1, false, initial_lines)

  -- Track cascade state per column
  local column_states = {}
  for col = 1, logo_width do
    column_states[col] = {
      current_row = 0, -- Current cascade position (0 = not started)
      final_chars = {}, -- Final characters for each row
      settled = false, -- Whether column has finished cascading
    }
    -- Pre-compute final characters for this column
    for row = 1, #M.logo_lines do
      local line = M.logo_lines[row]
      local chars = vim.fn.split(line, [[\zs]])
      column_states[col].final_chars[row] = chars[col] or " "
    end
  end

  local current_start_col = 0 -- Which column to start cascading

  M.state.timer = vim.loop.new_timer()
  M.state.timer:start(
    100,
    interval_ms,
    vim.schedule_wrap(function()
      if not vim.api.nvim_buf_is_valid(bufnr) then
        M.stop_animation()
        return
      end

      -- Start a new column cascade every few ticks
      if current_start_col < logo_width then
        current_start_col = current_start_col + 1
      end

      -- Update all active columns
      local all_settled = true
      for col = 1, current_start_col do
        local state = column_states[col]
        if not state.settled then
          all_settled = false

          if state.current_row < #M.logo_lines then
            state.current_row = state.current_row + 1
          else
            state.settled = true
          end
        end
      end

      -- Render current state
      for row = 1, #M.logo_lines do
        local line_idx = row
        local line_chars = {}

        for col = 1, logo_width do
          local state = column_states[col]
          if col > current_start_col then
            -- Column hasn't started yet
            table.insert(line_chars, " ")
          elseif state.settled or state.current_row >= row then
            -- Show final character
            table.insert(line_chars, state.final_chars[row])
          elseif state.current_row == row - 1 then
            -- Cascade head - show random character
            local rand_char = cascade_chars[math.random(#cascade_chars)]
            table.insert(line_chars, rand_char)
          else
            -- Above cascade head - show space
            table.insert(line_chars, " ")
          end
        end

        local new_line = table.concat(line_chars)
        vim.api.nvim_buf_set_lines(bufnr, line_idx, line_idx + 1, false, { new_line })
      end

      -- Check if animation is complete
      if all_settled and current_start_col >= logo_width then
        M.stop_animation()
        M.state.animation_complete = true
        if on_complete then
          on_complete()
        end
      end
    end)
  )
end

-- Continuous side-to-side scanning effect (runs forever until stopped)
function M.animate_logo_scan(bufnr, on_complete)
  M.stop_animation()

  M.state.bufnr = bufnr
  M.state.current_col = 0
  M.state.direction = 1
  M.state.animation_complete = false
  M.state.jiggle_count = 0
  M.state.paused_at_edge = false

  -- Create highlight namespace for the glow effect
  if not M.state.highlight_ns then
    M.state.highlight_ns = vim.api.nvim_create_namespace("agent_logo_scan")
  end

  local logo_width = get_logo_width()
  local glow_width = 5 -- Width of the "scanner" glow

  -- Set up buffer with full logo
  vim.api.nvim_buf_set_lines(bufnr, 0, -1, false, M.welcome_message)

  -- Define highlight groups for glow effect (bright center, fading edges)
  vim.api.nvim_set_hl(0, "AgentLogoGlow1", { fg = "#ffffff", bold = true })
  vim.api.nvim_set_hl(0, "AgentLogoGlow2", { fg = "#cccccc" })
  vim.api.nvim_set_hl(0, "AgentLogoGlow3", { fg = "#888888" })
  vim.api.nvim_set_hl(0, "AgentLogoDim", { fg = "#444444" })

  local function get_next_interval()
    -- If jiggling, use fast interval
    if M.state.jiggle_count > 0 then
      return 30
    end

    -- If at edge, small chance to pause
    local at_left_edge = M.state.current_col <= 1
    local at_right_edge = M.state.current_col >= logo_width - 2

    if at_left_edge or at_right_edge then
      -- 8% chance to pause at edge for 0.5-1 second
      if math.random() < 0.08 then
        M.state.paused_at_edge = true
        return math.random(500, 1000)
      end
    end

    M.state.paused_at_edge = false

    -- Smooth consistent movement
    return 25
  end

  M.state.timer = vim.loop.new_timer()

  local function update_highlights()
    -- Clear previous highlights
    vim.api.nvim_buf_clear_namespace(bufnr, M.state.highlight_ns, 0, -1)

    -- Apply glow highlights to logo lines (lines 1-4 in buffer, 0-indexed)
    for row = 1, #M.logo_lines do
      local line = M.logo_lines[row]
      local line_chars = vim.fn.split(line, [[\zs]])
      local byte_pos = 0

      for col = 1, #line_chars do
        local char = line_chars[col]
        local char_bytes = #char
        local dist = math.abs(col - M.state.current_col - 1)

        local hl_group = nil
        if dist == 0 then
          hl_group = "AgentLogoGlow1"
        elseif dist == 1 then
          hl_group = "AgentLogoGlow2"
        elseif dist == 2 then
          hl_group = "AgentLogoGlow3"
        elseif dist > glow_width then
          hl_group = "AgentLogoDim"
        end

        if hl_group and char ~= " " then
          vim.api.nvim_buf_add_highlight(bufnr, M.state.highlight_ns, hl_group, row, byte_pos, byte_pos + char_bytes)
        end

        byte_pos = byte_pos + char_bytes
      end
    end
  end

  local function do_tick()
    if not vim.api.nvim_buf_is_valid(bufnr) then
      M.stop_animation()
      return
    end

    -- Handle jiggle mode
    if M.state.jiggle_count > 0 then
      M.state.jiggle_count = M.state.jiggle_count - 1
      -- Alternate direction for jiggle effect
      M.state.current_col = M.state.current_col + M.state.jiggle_direction
      M.state.jiggle_direction = -M.state.jiggle_direction

      -- Clamp to bounds
      if M.state.current_col < 0 then
        M.state.current_col = 0
      elseif M.state.current_col >= logo_width then
        M.state.current_col = logo_width - 1
      end

      update_highlights()

      -- Schedule next jiggle tick
      if M.state.timer then
        M.state.timer:stop()
        M.state.timer:start(30, 0, vim.schedule_wrap(do_tick))
      end
      return
    end

    -- Normal scanning movement
    M.state.current_col = M.state.current_col + M.state.direction

    -- Bounce at edges
    if M.state.current_col >= logo_width then
      M.state.current_col = logo_width - 1
      M.state.direction = -1
    elseif M.state.current_col < 0 then
      M.state.current_col = 0
      M.state.direction = 1
    end

    update_highlights()

    -- Schedule next tick with potentially different interval
    if M.state.timer then
      M.state.timer:stop()
      local next_interval = get_next_interval()
      M.state.timer:start(next_interval, 0, vim.schedule_wrap(do_tick))
    end
  end

  -- Start the animation loop
  M.state.timer:start(100, 0, vim.schedule_wrap(do_tick))
end

-- Trigger a jiggle effect (call this when user types)
function M.jiggle()
  if not M.is_animating() then
    return
  end

  -- Set jiggle count (number of back-and-forth movements)
  M.state.jiggle_count = math.random(2, 4)
  M.state.jiggle_direction = (math.random() < 0.5) and 1 or -1
end

-- Simple typewriter effect (character by character, left to right, top to bottom)
function M.animate_logo_typewriter(bufnr, on_complete)
  M.stop_animation()

  M.state.bufnr = bufnr
  M.state.animation_complete = false

  local interval_ms = 15 -- Speed per character

  -- Build flat list of all characters with positions
  local char_queue = {}
  for row, line in ipairs(M.logo_lines) do
    local chars = vim.fn.split(line, [[\zs]])
    for col, char in ipairs(chars) do
      table.insert(char_queue, { row = row, col = col, char = char })
    end
  end

  local current_idx = 0

  -- Initialize buffer with empty logo
  local initial_lines = { "```" }
  for _, line in ipairs(M.logo_lines) do
    table.insert(initial_lines, string.rep(" ", vim.fn.strchars(line)))
  end
  table.insert(initial_lines, "```")
  table.insert(initial_lines, "")
  table.insert(initial_lines, "> Type your request in the prompt below.")

  vim.api.nvim_buf_set_lines(bufnr, 0, -1, false, initial_lines)

  -- Current buffer state (mutable)
  local buffer_lines = {}
  for _, line in ipairs(M.logo_lines) do
    local chars = {}
    for _ = 1, vim.fn.strchars(line) do
      table.insert(chars, " ")
    end
    table.insert(buffer_lines, chars)
  end

  M.state.timer = vim.loop.new_timer()
  M.state.timer:start(
    100,
    interval_ms,
    vim.schedule_wrap(function()
      if not vim.api.nvim_buf_is_valid(bufnr) then
        M.stop_animation()
        return
      end

      -- Process multiple characters per tick for speed
      local chars_per_tick = 2
      for _ = 1, chars_per_tick do
        current_idx = current_idx + 1
        if current_idx > #char_queue then
          break
        end

        local item = char_queue[current_idx]
        buffer_lines[item.row][item.col] = item.char

        -- Update the line in buffer
        local new_line = table.concat(buffer_lines[item.row])
        vim.api.nvim_buf_set_lines(bufnr, item.row, item.row + 1, false, { new_line })
      end

      if current_idx >= #char_queue then
        M.stop_animation()
        M.state.animation_complete = true
        if on_complete then
          on_complete()
        end
      end
    end)
  )
end

-- Stop any running animation
function M.stop_animation()
  if M.state.timer then
    M.state.timer:stop()
    M.state.timer:close()
    M.state.timer = nil
  end
end

-- Check if animation is currently running
function M.is_animating()
  return M.state.timer ~= nil
end

-- Immediately show the full welcome message (skip animation)
function M.show_static(bufnr)
  M.stop_animation()
  vim.api.nvim_buf_set_lines(bufnr, 0, -1, false, M.welcome_message)
  M.state.animation_complete = true
end

return M
