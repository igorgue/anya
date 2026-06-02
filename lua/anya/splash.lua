-- Splash screen module for Anya
-- Displays an animated Conway's Game of Life in a floating window with color effects
local M = {}

-- Configuration (feel free to tweak these)
M.interval = 1000  -- milliseconds between Game of Life generations
M.orbit_speed = 0.02  -- counter-clockwise orbit speed (radians per frame)
M.grid_size = 20  -- 20x20 grid
M.min_grid_size = 4  -- smallest responsive grid before hiding the splash
M.float_width = 60  -- preferred float width; shrinks in narrow panes
M.horizontal_padding = 2  -- spaces on each side of the grid/footer
M.footer_padding_bottom = 1  -- keep the footer visually clear in small panes

-- State
M.state = {
  win = nil,
  buf = nil,
  timer = nil,
  grid = {},
  active_grid_size = M.grid_size,
  horizontal_padding = M.horizontal_padding,
  orbit_angle = 0,
  prompt_cursor = { row = 0, col = 0 },
  prompt_buf = nil,
  cursor_timer = nil,
  augroup = nil,
}

-- Highlight groups
local HL_ORBIT = "AnyaSplashOrbit"
local HL_CURSOR = "AnyaSplashCursor"
local HL_BASE = "Comment"
local HL_PALETTE = {
  "AnyaSplashString",
  "AnyaSplashConstant",
  "AnyaSplashIdentifier",
  "AnyaSplashStatement",
  "AnyaSplashPreProc",
  "AnyaSplashType",
}
local HL_SOURCE_GROUPS = {
  "String",
  "Constant",
  "Identifier",
  "Statement",
  "PreProc",
  "Type",
}

--- Initialize highlight groups
local function init_highlights()
  -- Orbiting light: bright cyan/white
  vim.cmd(string.format([[
    highlight %s guifg=#87ceeb gui=bold
  ]], HL_ORBIT))
  
  for i, group in ipairs(HL_SOURCE_GROUPS) do
    local ok_source, source = pcall(vim.api.nvim_get_hl, 0, { name = group, link = false })
    if ok_source and source and source.fg then
      vim.api.nvim_set_hl(0, HL_PALETTE[i], { fg = source.fg, bold = i % 2 == 0 })
    else
      vim.api.nvim_set_hl(0, HL_PALETTE[i], { link = HL_BASE })
    end
  end

  -- Cursor-following: use FloatBorder color
  local ok, float_border = pcall(vim.api.nvim_get_hl, 0, { name = "FloatBorder", link = false })
  if ok and float_border and float_border.fg then
    local hex = string.format("#%06x", float_border.fg)
    vim.cmd(string.format([[
      highlight %s guifg=%s gui=bold
    ]], HL_CURSOR, hex))
  else
    vim.cmd(string.format([[
      highlight %s guifg=#7cb8bb gui=bold
    ]], HL_CURSOR))
  end
end

--- Initialize the grid with random cells
local function init_grid()
  -- Use current time with microseconds for better randomness
  math.randomseed(math.floor(vim.loop.hrtime() / 1000))
  M.state.grid = {}
  for i = 1, M.state.active_grid_size do
    M.state.grid[i] = {}
    for j = 1, M.state.active_grid_size do
      M.state.grid[i][j] = math.random() > 0.7 and 1 or 0
    end
  end
end

--- Count alive neighbors for a cell (toroidal wrapping)
local function count_neighbors(row, col)
  local count = 0
  for dr = -1, 1 do
    for dc = -1, 1 do
      if not (dr == 0 and dc == 0) then
        local r = row + dr
        local c = col + dc
        -- Wrap around edges
        if r < 1 then r = M.state.active_grid_size end
        if r > M.state.active_grid_size then r = 1 end
        if c < 1 then c = M.state.active_grid_size end
        if c > M.state.active_grid_size then c = 1 end
        count = count + (M.state.grid[r][c] or 0)
      end
    end
  end
  return count
end

--- Compute next generation using Conway's Game of Life rules
local function next_generation()
  local new_grid = {}
  for i = 1, M.state.active_grid_size do
    new_grid[i] = {}
    for j = 1, M.state.active_grid_size do
      local neighbors = count_neighbors(i, j)
      if M.state.grid[i][j] == 1 then
        -- Cell is alive: survives with 2 or 3 neighbors
        new_grid[i][j] = (neighbors == 2 or neighbors == 3) and 1 or 0
      else
        -- Cell is dead: born with exactly 3 neighbors
        new_grid[i][j] = neighbors == 3 and 1 or 0
      end
    end
  end
  M.state.grid = new_grid
end

--- Calculate distance between two points
local function distance(r1, c1, r2, c2)
  return math.sqrt((r1 - r2)^2 + (c2 - c1)^2)
end

--- Get the cell character (always ■ with trailing space for 2-char width)
local function cell_char()
  return "■ "
end

--- Calculate orbit light position (counter-clockwise circle)
local function get_orbit_pos()
  local center = (M.state.active_grid_size + 1) / 2
  local radius = M.state.active_grid_size * 0.4
  local x = center + radius * math.cos(M.state.orbit_angle)
  local y = center + radius * math.sin(M.state.orbit_angle)
  return y, x  -- row, col
end

--- Render the grid to buffer lines
local function render_grid()
  if not M.state.buf or not vim.api.nvim_buf_is_valid(M.state.buf) then
    return
  end
  
  local lines = {}
  local highlights = {}
  
  -- Get orbit position
  local orbit_row, orbit_col = get_orbit_pos()
  
  for i = 1, M.state.active_grid_size do
    local line = ""
    for j = 1, M.state.active_grid_size do
      if M.state.grid[i][j] == 1 then
        line = line .. cell_char()
        
        -- Calculate distances for color intensity
        local dist_orbit = distance(i, j, orbit_row, orbit_col)
        local dist_cursor = distance(i, j, M.state.prompt_cursor.row, M.state.prompt_cursor.col)
        
        -- Determine highlight based on proximity to light sources
        local max_dist = M.state.active_grid_size * 0.5
        local orbit_intensity = math.max(0, 1 - dist_orbit / max_dist)
        local cursor_intensity = math.max(0, 1 - dist_cursor / max_dist)
        
        local palette_index = ((i + j + math.floor(M.state.orbit_angle * 3)) % #HL_PALETTE) + 1
        local hl_group = HL_PALETTE[palette_index]
        if orbit_intensity > cursor_intensity and orbit_intensity > 0.42 then
          hl_group = HL_ORBIT
        elseif cursor_intensity > 0.42 then
          hl_group = HL_CURSOR
        end
        
        local buf_col = (j - 1) * 2 + M.state.horizontal_padding  -- each cell is 2 chars
        table.insert(highlights, {
          group = hl_group,
          row = i - 1,  -- 0-indexed
          col = buf_col,
          length = 2
        })
      else
        line = line .. "  "  -- Two spaces for dead cells
      end
    end
    table.insert(lines, string.rep(" ", M.state.horizontal_padding) .. line)
  end
  
  -- Add empty line and footer
  table.insert(lines, "")
  local footer_text = "Type your request..."
  local content_width = M.state.active_grid_size * 2
  local footer_col = M.state.horizontal_padding + math.max(0, math.floor((content_width - #footer_text) / 2))
  local footer = string.rep(" ", footer_col) .. footer_text
  table.insert(lines, footer)
  local footer_row = #lines - 1
  table.insert(highlights, {
    group = HL_BASE,
    row = footer_row,
    col = footer_col,
    length = #footer_text
  })

  for _ = 1, M.footer_padding_bottom do
    table.insert(lines, "")
  end
  
  -- Update buffer
  vim.api.nvim_buf_set_option(M.state.buf, "modifiable", true)
  vim.api.nvim_buf_set_lines(M.state.buf, 0, -1, false, lines)
  vim.api.nvim_buf_set_option(M.state.buf, "modifiable", false)
  
  -- Apply highlights
  vim.api.nvim_buf_clear_namespace(M.state.buf, -1, 0, -1)
  for _, hl in ipairs(highlights) do
    vim.api.nvim_buf_add_highlight(M.state.buf, -1, hl.group, hl.row, hl.col, hl.col + hl.length)
  end
end

--- Animation tick: update game state and render
local function tick()
  if not M.state.win or not vim.api.nvim_win_is_valid(M.state.win) then
    M.hide()
    return
  end
  
  -- Update orbit angle (counter-clockwise = increasing angle)
  M.state.orbit_angle = M.state.orbit_angle + M.orbit_speed
  if M.state.orbit_angle > 2 * math.pi then
    M.state.orbit_angle = M.state.orbit_angle - 2 * math.pi
  end
  
  -- Next generation
  next_generation()
  
  -- Render
  render_grid()
end

--- Track cursor position in prompt buffer
local function setup_cursor_tracking()
  -- Find prompt buffer
  local prompt_buf = nil
  for _, buf in ipairs(vim.api.nvim_list_bufs()) do
    if vim.api.nvim_buf_is_valid(buf) then
      local ft = vim.api.nvim_get_option_value("filetype", { buf = buf })
      if ft == "anya-prompt" then
        prompt_buf = buf
        break
      end
    end
  end
  
  if not prompt_buf then
    return
  end
  
  M.state.prompt_buf = prompt_buf
  
  -- Update cursor position periodically
  M.state.cursor_timer = vim.loop.new_timer()
  M.state.cursor_timer:start(0, 100, vim.schedule_wrap(function()
    if not M.state.win or not vim.api.nvim_win_is_valid(M.state.win) then
      if M.state.cursor_timer then
        M.state.cursor_timer:stop()
        M.state.cursor_timer = nil
      end
      return
    end
    
    -- Get cursor position in prompt buffer if available
    if M.state.prompt_buf and vim.api.nvim_buf_is_valid(M.state.prompt_buf) then
      -- Find window for prompt buffer
      local prompt_win = nil
      for _, win in ipairs(vim.api.nvim_list_wins()) do
        if vim.api.nvim_win_is_valid(win) then
          if vim.api.nvim_win_get_buf(win) == M.state.prompt_buf then
            prompt_win = win
            break
          end
        end
      end
      
      if prompt_win then
        local ok, cursor = pcall(vim.api.nvim_win_get_cursor, prompt_win)
        if ok then
          M.state.prompt_cursor = {
            row = math.floor(cursor[1] / 2),
            col = math.floor(cursor[2] / 4)
          }
        end
      end
    end
  end))
end

local function calculate_layout(chat_win)
  local win_width = vim.api.nvim_win_get_width(chat_win)
  local win_height = vim.api.nvim_win_get_height(chat_win)
  local reserved_height = 2 + M.footer_padding_bottom -- blank line + footer + bottom padding

  local max_grid_by_height = win_height - reserved_height
  local max_grid_by_width = math.floor((win_width - (M.horizontal_padding * 2)) / 2)
  local grid_size = math.min(M.grid_size, max_grid_by_height, max_grid_by_width)

  if grid_size < M.min_grid_size then
    return nil
  end

  local content_width = grid_size * 2 + (M.horizontal_padding * 2)
  local splash_width = math.min(M.float_width, win_width, math.max(content_width, #"Type your request..." + (M.horizontal_padding * 2)))
  local splash_height = math.min(win_height, grid_size + reserved_height)

  return {
    grid_size = grid_size,
    width = splash_width,
    height = splash_height,
    row = math.max(0, math.floor((win_height - splash_height) / 2)),
    col = math.max(0, math.floor((win_width - splash_width) / 2)),
    padding = math.max(0, math.floor((splash_width - (grid_size * 2)) / 2)),
  }
end

--- Show the splash screen
function M.show()
  -- Don't show if already showing
  if M.state.win and vim.api.nvim_win_is_valid(M.state.win) then
    return
  end
  
  -- Find the chat buffer
  local chat_buf = nil
  for _, buf in ipairs(vim.api.nvim_list_bufs()) do
    if vim.api.nvim_buf_is_valid(buf) then
      local ft = vim.api.nvim_get_option_value("filetype", { buf = buf })
      if ft == "anya-chat" then
        chat_buf = buf
        break
      end
    end
  end
  
  if not chat_buf then
    return
  end
  
  -- Check if chat buffer is empty
  local lines = vim.api.nvim_buf_get_lines(chat_buf, 0, -1, false)
  local is_empty = true
  for _, line in ipairs(lines) do
    if line:match("%S") then
      is_empty = false
      break
    end
  end
  
  if not is_empty then
    return
  end
  
  -- Find chat window
  local chat_win = nil
  for _, win in ipairs(vim.api.nvim_list_wins()) do
    if vim.api.nvim_win_is_valid(win) then
      local buf = vim.api.nvim_win_get_buf(win)
      if buf == chat_buf then
        chat_win = win
        break
      end
    end
  end
  
  if not chat_win then
    return
  end
  
  -- Initialize highlights
  init_highlights()
  
  -- Create the splash buffer
  M.state.buf = vim.api.nvim_create_buf(false, true)
  vim.api.nvim_buf_set_option(M.state.buf, "buftype", "nofile")
  vim.api.nvim_buf_set_option(M.state.buf, "swapfile", false)
  vim.api.nvim_buf_set_option(M.state.buf, "modifiable", false)
  
  -- Calculate position (centered on chat window)
  local win_width = vim.api.nvim_win_get_width(chat_win)
  local win_height = vim.api.nvim_win_get_height(chat_win)
  
  local layout = calculate_layout(chat_win)
  if not layout then
    return
  end

  -- Create floating window
  M.state.win = vim.api.nvim_open_win(M.state.buf, false, {
    relative = "win",
    win = chat_win,
    width = layout.width,
    height = layout.height,
    row = layout.row,
    col = layout.col,
    style = "minimal",
    border = "none",
    zindex = 50,
  })
  
  -- Keep the floating window background transparent/inherited; text is highlighted separately.
  vim.api.nvim_win_set_option(M.state.win, "winhl", "NormalFloat:Normal,FloatBorder:FloatBorder")
  vim.api.nvim_win_set_option(M.state.win, "wrap", false)
  vim.api.nvim_win_set_option(M.state.win, "winblend", 0)
  
  -- Initialize grid with random seed
  M.state.active_grid_size = layout.grid_size
  M.state.horizontal_padding = layout.padding
  init_grid()
  
  -- Setup cursor tracking
  setup_cursor_tracking()
  
  -- Start animation timer
  M.state.timer = vim.loop.new_timer()
  M.state.timer:start(0, M.interval, vim.schedule_wrap(tick))
end

--- Hide the splash screen
function M.hide()
  -- Stop timers
  if M.state.timer then
    M.state.timer:stop()
    M.state.timer = nil
  end
  
  if M.state.cursor_timer then
    M.state.cursor_timer:stop()
    M.state.cursor_timer = nil
  end
  
  -- Close window
  if M.state.win and vim.api.nvim_win_is_valid(M.state.win) then
    vim.api.nvim_win_close(M.state.win, true)
  end
  
  -- Delete buffer
  if M.state.buf and vim.api.nvim_buf_is_valid(M.state.buf) then
    vim.api.nvim_buf_delete(M.state.buf, { force = true })
  end
  
  M.state.win = nil
  M.state.buf = nil
  M.state.prompt_buf = nil
end

--- Check if splash is currently showing
function M.is_showing()
  return M.state.win and vim.api.nvim_win_is_valid(M.state.win)
end

local function is_anya_chat(bufnr)
  if not bufnr or not vim.api.nvim_buf_is_valid(bufnr) then
    return false
  end
  local ok, ft = pcall(vim.api.nvim_get_option_value, "filetype", { buf = bufnr })
  return ok and ft == "anya-chat"
end

local function is_empty_chat(bufnr)
  if not is_anya_chat(bufnr) then
    return false
  end
  local ok, lines = pcall(vim.api.nvim_buf_get_lines, bufnr, 0, -1, false)
  if not ok then
    return false
  end
  for _, line in ipairs(lines) do
    if line:match("%S") then
      return false
    end
  end
  return true
end

--- Show the splash if the visible Anya chat buffer is empty.
function M.show_if_empty()
  vim.schedule(function()
    if M.is_showing() then
      return
    end

    for _, win in ipairs(vim.api.nvim_list_wins()) do
      if vim.api.nvim_win_is_valid(win) then
        local ok, bufnr = pcall(vim.api.nvim_win_get_buf, win)
        if ok and is_empty_chat(bufnr) then
          M.show()
          return
        end
      end
    end
  end)
end

--- Auto-hide when chat receives content and re-open when an empty chat is shown.
function M.setup_autocmd()
  if M.state.augroup then
    pcall(vim.api.nvim_del_augroup_by_id, M.state.augroup)
  end

  M.state.augroup = vim.api.nvim_create_augroup("AnyaSplash", { clear = true })

  vim.api.nvim_create_autocmd({ "TextChanged", "TextChangedI", "TextChangedP", "BufModifiedSet" }, {
    group = M.state.augroup,
    callback = function(args)
      if is_anya_chat(args.buf) then
        if is_empty_chat(args.buf) then
          M.show_if_empty()
        elseif M.is_showing() then
          M.hide()
        end
      end
    end,
  })

  vim.api.nvim_create_autocmd({ "BufEnter", "WinEnter", "FileType" }, {
    group = M.state.augroup,
    callback = function(args)
      if is_empty_chat(args.buf) then
        M.show_if_empty()
      end
    end,
  })

  vim.api.nvim_create_autocmd("WinResized", {
    group = M.state.augroup,
    callback = function()
      if M.is_showing() then
        M.hide()
        M.show_if_empty()
      else
        M.show_if_empty()
      end
    end,
  })

  vim.api.nvim_create_autocmd("WinClosed", {
    group = M.state.augroup,
    callback = function()
      if M.is_showing() then
        vim.schedule(function()
          local has_visible_empty_chat = false
          for _, win in ipairs(vim.api.nvim_list_wins()) do
            if vim.api.nvim_win_is_valid(win) then
              local ok, bufnr = pcall(vim.api.nvim_win_get_buf, win)
              if ok and is_empty_chat(bufnr) then
                has_visible_empty_chat = true
                break
              end
            end
          end
          if not has_visible_empty_chat then
            M.hide()
          end
        end)
      end
    end,
  })
end

--- Toggle the splash screen
function M.toggle()
  vim.schedule(function()
    if M.is_showing() then
      M.hide()
    else
      M.show()
    end
  end)
end

vim.schedule(function()
  M.setup_autocmd()
  M.show_if_empty()
end)

return M
