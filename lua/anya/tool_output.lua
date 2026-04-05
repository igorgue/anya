-- Tool output viewing utilities for Anya plugin
-- Opens tool outputs in Snacks scratch buffers

local M = {}
local ui_utils = require("anya.ui_utils")

--- Sanitize a title for use as a filename (matches Python's _sanitize_title)
--- Lowercase, replace non-alphanumeric sequences with hyphens, strip leading/trailing hyphens.
--- @param title string The title to sanitize
--- @return string Sanitized filename stem
local function sanitize_title(title)
  title = title:lower()
  title = title:match("^%s*(.-)%s*$") or "" -- trim
  title = title:gsub("[^a-z0-9]+", "-")
  title = title:match("^%-*(.-)%-*$") or "" -- strip leading/trailing hyphens
  return title ~= "" and title or "untitled"
end

--- Send a retry prompt for the code in the scratch buffer to Anya
--- @param buf number Buffer number containing the code
--- @param win number Window number (from Snacks scratch self.win)
--- @param title string The code title (used in [[title]] reference)
local function retry_code_with_anya(buf, win, title, extra_instructions)
  local content = table.concat(vim.api.nvim_buf_get_lines(buf, 0, -1, false), "\n")
  local prompt = string.format("Retry this code [[%s]]:\n\n```python\n%s\n```", title, content)

  if extra_instructions and extra_instructions:match("%S") then
    prompt = prompt .. "\n\nAdditional instructions:\n" .. extra_instructions
  end

  -- Find the prompt buffer
  local prompt_buf = nil
  for _, b in ipairs(vim.api.nvim_list_bufs()) do
    if vim.api.nvim_buf_is_valid(b) then
      local ft = vim.api.nvim_get_option_value("filetype", { buf = b })
      if ft == "anya-prompt" then
        prompt_buf = b
        break
      end
    end
  end

  if not prompt_buf then
    vim.notify("Anya: Open Anya first with :Anya, then retry.", vim.log.levels.WARN)
    return
  end

  -- Close the scratch window before sending
  if win and vim.api.nvim_win_is_valid(win) then
    vim.api.nvim_win_close(win, true)
  end

  vim.api.nvim_set_option_value("modifiable", true, { buf = prompt_buf })
  vim.api.nvim_buf_set_lines(prompt_buf, 0, -1, false, vim.split(prompt, "\n", { plain = true }))
  require("anya.conversation").send_message()
end

local function retry_code_with_anya_prompt(buf, win, title)
  vim.ui.input({
    prompt = "Extra instructions for retry: ",
  }, function(input)
    retry_code_with_anya(buf, win, title, input)
  end)
end

--- Open the saved code file for a [[title]] reference using Snacks scratch.
--- The file lives at <cwd>/.anya/code/<sanitized-title>.py
--- @param override_line? number Optional 1-indexed line (e.g. from getmousepos)
--- @param override_col? number Optional 1-indexed column (e.g. from getmousepos)
--- @return boolean True if a code file was opened, false otherwise
function M.open_code_at_cursor(override_line, override_col)
  local cursor = vim.api.nvim_win_get_cursor(0)
  local line_num = override_line or cursor[1]
  local col = override_col or (cursor[2] + 1) -- convert to 1-indexed
  local bufnr = vim.api.nvim_get_current_buf()
  local line = vim.api.nvim_buf_get_lines(bufnr, line_num - 1, line_num, false)[1] or ""

  -- Collect all [[title]] occurrences on this line
  local all_titles = {}
  local pos = 1
  while true do
    local s, e, title = line:find("%[%[(.-)%]%]", pos)
    if not s then
      break
    end
    table.insert(all_titles, { s = s, e = e, title = title })
    pos = e + 1
  end

  -- Pick the title whose brackets contain the cursor col; if none, fall back to
  -- the only title on the line (so pressing <CR> anywhere on a [[title]] line works).
  local best_title = nil
  for _, t in ipairs(all_titles) do
    if col >= t.s and col <= t.e then
      best_title = t.title
      break
    end
  end
  if not best_title and #all_titles == 1 then
    best_title = all_titles[1].title
  end

  local title = best_title
  if not title or title == "" then
    return false
  end

  local cwd = vim.fn.getcwd()
  local sanitized = sanitize_title(title)

  -- Glob for all versioned files matching <sanitized>-<hash>.py
  local pattern = cwd .. "/.anya/code/" .. sanitized .. "-*.py"
  local matches = vim.fn.glob(pattern, false, true)
  if not matches or #matches == 0 then
    -- File not saved yet (tool still running or never executed). Consume the
    -- keypress so that 'normal! za' is NOT triggered on the message fold.
    vim.notify("Anya: No code file found for [[" .. title .. "]] yet.", vim.log.levels.INFO)
    return true
  end

  -- Pick the most recently modified file
  table.sort(matches, function(a, b)
    return vim.fn.getftime(a) > vim.fn.getftime(b)
  end)
  local file_path = matches[1]

  local snacks_ok, Snacks = pcall(require, "snacks")
  if snacks_ok and Snacks.scratch then
    Snacks.scratch.open({
      name = "Code: " .. title,
      ft = "python",
      icon = "󰌠",
      file = file_path,
      win = {
        style = "scratch",
        wo = { winhighlight = "NormalFloat:Normal" },
        keys = {
          retry_with_anya = {
            "gs",
            function(self)
              retry_code_with_anya(self.buf, self.win, title)
            end,
            desc = "retry",
            mode = { "n" },
          },
          retry_with_anya_prompt = {
            "gS",
            function(self)
              retry_code_with_anya_prompt(self.buf, self.win, title)
            end,
            desc = "retry with prompt",
            mode = { "n" },
          },
          open_output = {
            "go",
            function(self)
              self:close()
              vim.schedule(function()
                M.open_output_for_code(file_path, title)
              end)
            end,
            desc = "output",
            mode = { "n" },
          },
        },
      },
    })
  else
    -- Fallback: open in a vertical split
    vim.cmd("vsplit " .. vim.fn.fnameescape(file_path))
  end

  return true
end

--- Open the code file for a given title directly (used by `gc` in output scratch).
--- @param title string The code title
function M.open_code_at_cursor_by_title(title)
  local cwd = vim.fn.getcwd()
  local sanitized = sanitize_title(title)

  local pattern = cwd .. "/.anya/code/" .. sanitized .. "-*.py"
  local matches = vim.fn.glob(pattern, false, true)
  if not matches or #matches == 0 then
    vim.notify("Anya: No code file found for [[" .. title .. "]].", vim.log.levels.INFO)
    return
  end

  table.sort(matches, function(a, b)
    return vim.fn.getftime(a) > vim.fn.getftime(b)
  end)
  local file_path = matches[1]

  local snacks_ok, Snacks = pcall(require, "snacks")
  if snacks_ok and Snacks.scratch then
    Snacks.scratch.open({
      name = "Code: " .. title,
      ft = "python",
      icon = "󰌠",
      file = file_path,
      win = {
        style = "scratch",
        wo = { winhighlight = "NormalFloat:Normal" },
        keys = {
          retry_with_anya = {
            "gs",
            function(self)
              retry_code_with_anya(self.buf, self.win, title)
            end,
            desc = "retry",
            mode = { "n" },
          },
          retry_with_anya_prompt = {
            "gS",
            function(self)
              retry_code_with_anya_prompt(self.buf, self.win, title)
            end,
            desc = "retry with prompt",
            mode = { "n" },
          },
          open_output = {
            "go",
            function(self)
              self:close()
              vim.schedule(function()
                M.open_output_for_code(file_path, title)
              end)
            end,
            desc = "output",
            mode = { "n" },
          },
        },
      },
    })
  else
    vim.cmd("vsplit " .. vim.fn.fnameescape(file_path))
  end
end

--- Open the output file corresponding to a code file.
--- Code lives at .anya/code/<name>-<hash>.py, output at .anya/output/<name>-<hash>.txt
--- @param code_path string Path to the code file
--- @param title string The code title
function M.open_output_for_code(code_path, title)
  -- Extract the <name>-<hash> stem from the code path
  local stem = vim.fn.fnamemodify(code_path, ":t:r") -- e.g. "my-script-a1b2c3d4"
  local cwd = vim.fn.getcwd()
  local output_path = cwd .. "/.anya/output/" .. stem .. ".txt"

  if vim.fn.filereadable(output_path) ~= 1 then
    vim.notify("Anya: No output file found for [[" .. title .. "]] yet.", vim.log.levels.INFO)
    return
  end

  local lines = vim.fn.readfile(output_path)

  local snacks_ok, Snacks = pcall(require, "snacks")
  if snacks_ok and Snacks.scratch then
    local win = Snacks.scratch.open({
      name = "Output: " .. title,
      ft = "text",
      icon = ui_utils.icons.tool_output,
      autowrite = false,
      filekey = {
        id = "output-" .. stem,
        cwd = false,
        branch = false,
        count = false,
      },
      win = {
        style = "scratch",
        wo = { winhighlight = "NormalFloat:Normal" },
        bo = { buftype = "nofile", bufhidden = "hide", swapfile = false, filetype = "text" },
        keys = {
          go_to_code = {
            "gc",
            function(self)
              self:close()
              vim.schedule(function()
                M.open_code_at_cursor_by_title(title)
              end)
            end,
            desc = "code",
            mode = { "n" },
          },
        },
      },
    })

    if win and win.buf then
      vim.bo[win.buf].modifiable = true
      vim.api.nvim_buf_set_lines(win.buf, 0, -1, false, vim.split(table.concat(lines, "\n"), "\n", { plain = true }))
      vim.bo[win.buf].modifiable = false
      vim.bo[win.buf].readonly = true
    end
  else
    M._open_simple_scratch(lines, "Output: " .. title, "text")
  end
end

--- Open the output file for a [[title]] reference at cursor position.
--- @param override_line? number Optional 1-indexed line (e.g. from getmousepos)
--- @param override_col? number Optional 1-indexed column (e.g. from getmousepos)
--- @return boolean True if an output was opened, false otherwise
function M.open_output_at_cursor(override_line, override_col)
  local cursor = vim.api.nvim_win_get_cursor(0)
  local line_num = override_line or cursor[1]
  local col = override_col or (cursor[2] + 1) -- convert to 1-indexed
  local bufnr = vim.api.nvim_get_current_buf()
  local line = vim.api.nvim_buf_get_lines(bufnr, line_num - 1, line_num, false)[1] or ""

  -- Collect all [[title]] occurrences on this line
  local all_titles = {}
  local pos = 1
  while true do
    local s, e, t = line:find("%[%[(.-)%]%]", pos)
    if not s then
      break
    end
    table.insert(all_titles, { s = s, e = e, title = t })
    pos = e + 1
  end

  local best_title = nil
  for _, t in ipairs(all_titles) do
    if col >= t.s and col <= t.e then
      best_title = t.title
      break
    end
  end
  if not best_title and #all_titles == 1 then
    best_title = all_titles[1].title
  end

  if not best_title or best_title == "" then
    return false
  end

  local cwd = vim.fn.getcwd()
  local sanitized = sanitize_title(best_title)

  -- Find the most recent matching output file
  local pattern = cwd .. "/.anya/output/" .. sanitized .. "-*.txt"
  local matches = vim.fn.glob(pattern, false, true)
  if not matches or #matches == 0 then
    vim.notify("Anya: No output file found for [[" .. best_title .. "]] yet.", vim.log.levels.INFO)
    return true
  end

  table.sort(matches, function(a, b)
    return vim.fn.getftime(a) > vim.fn.getftime(b)
  end)
  local output_path = matches[1]
  local lines = vim.fn.readfile(output_path)
  local stem = vim.fn.fnamemodify(output_path, ":t:r")

  local snacks_ok, Snacks = pcall(require, "snacks")
  if snacks_ok and Snacks.scratch then
    local win = Snacks.scratch.open({
      name = "Output: " .. best_title,
      ft = "text",
      icon = ui_utils.icons.tool_output,
      autowrite = false,
      filekey = {
        id = "output-" .. stem,
        cwd = false,
        branch = false,
        count = false,
      },
      win = {
        style = "scratch",
        wo = { winhighlight = "NormalFloat:Normal" },
        bo = { buftype = "nofile", bufhidden = "hide", swapfile = false, filetype = "text" },
        keys = {
          go_to_code = {
            "gc",
            function(self)
              self:close()
              vim.schedule(function()
                M.open_code_at_cursor_by_title(best_title)
              end)
            end,
            desc = "code",
            mode = { "n" },
          },
        },
      },
    })

    if win and win.buf then
      vim.bo[win.buf].modifiable = true
      vim.api.nvim_buf_set_lines(win.buf, 0, -1, false, vim.split(table.concat(lines, "\n"), "\n", { plain = true }))
      vim.bo[win.buf].modifiable = false
      vim.bo[win.buf].readonly = true
    end
  else
    M._open_simple_scratch(lines, "Output: " .. best_title, "text")
  end
  return true
end

return M
