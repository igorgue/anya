local M = {}

---Redirect focus back to a float window if entering a non-float window
---If already trying to leave a float, cycle to the next float
function M.redirect_to_float()
  local current_win = vim.api.nvim_get_current_win()
  local current_buf = vim.api.nvim_win_get_buf(current_win)
  local ft = vim.api.nvim_buf_get_option(current_buf, "filetype")

  -- If we're in a float window, update tracking and don't redirect
  if ft == "anya-chat" or ft == "anya-prompt" then
    vim.g.anya_last_float_ft = ft
    return
  end

  -- We've entered a non-float window, redirect to the other float
  -- Try to find and cycle through floats
  local prev_float_ft = vim.g.anya_last_float_ft or "anya-prompt"

  local windows = vim.api.nvim_list_wins()

  -- If last window was prompt, go to chat; if chat, go to prompt
  local target_ft = (prev_float_ft == "anya-prompt") and "anya-chat" or "anya-prompt"

  -- Try to find target float
  for _, win_id in ipairs(windows) do
    if vim.api.nvim_win_is_valid(win_id) then
      local buf = vim.api.nvim_win_get_buf(win_id)
      local buf_ft = vim.api.nvim_buf_get_option(buf, "filetype")
      if buf_ft == target_ft then
        vim.api.nvim_set_current_win(win_id)
        vim.g.anya_last_float_ft = target_ft
        return
      end
    end
  end

  -- Fallback: find any float window
  for _, win_id in ipairs(windows) do
    if vim.api.nvim_win_is_valid(win_id) then
      local buf = vim.api.nvim_win_get_buf(win_id)
      local buf_ft = vim.api.nvim_buf_get_option(buf, "filetype")
      if buf_ft == "anya-prompt" or buf_ft == "anya-chat" then
        vim.api.nvim_set_current_win(win_id)
        vim.g.anya_last_float_ft = buf_ft
        return
      end
    end
  end
end

---Toggle between chat and prompt windows
function M.toggle_focus()
  local current_win = vim.api.nvim_get_current_win()
  local current_buf = vim.api.nvim_win_get_buf(current_win)
  local current_ft = vim.api.nvim_buf_get_option(current_buf, "filetype")

  local target_ft = (current_ft == "anya-chat") and "anya-prompt" or "anya-chat"

  local windows = vim.api.nvim_list_wins()
  for _, win_id in ipairs(windows) do
    if vim.api.nvim_win_is_valid(win_id) then
      local buf = vim.api.nvim_win_get_buf(win_id)
      local ft = vim.api.nvim_buf_get_option(buf, "filetype")
      if ft == target_ft then
        vim.api.nvim_set_current_win(win_id)
        vim.g.anya_last_float_ft = target_ft
        return
      end
    end
  end
end

---Focus the chat window
function M.focus_chat()
  local current_win = vim.api.nvim_get_current_win()

  local windows = vim.api.nvim_list_wins()
  for _, win_id in ipairs(windows) do
    if vim.api.nvim_win_is_valid(win_id) then
      local buf = vim.api.nvim_win_get_buf(win_id)
      local ft = vim.api.nvim_buf_get_option(buf, "filetype")
      if ft == "anya-chat" and win_id ~= current_win then
        vim.api.nvim_set_current_win(win_id)
        vim.g.anya_last_float_ft = "anya-chat"
        return
      end
    end
  end
end

---Focus the prompt window
function M.focus_prompt()
  local current_win = vim.api.nvim_get_current_win()

  local windows = vim.api.nvim_list_wins()
  for _, win_id in ipairs(windows) do
    if vim.api.nvim_win_is_valid(win_id) then
      local buf = vim.api.nvim_win_get_buf(win_id)
      local ft = vim.api.nvim_buf_get_option(buf, "filetype")
      if ft == "anya-prompt" and win_id ~= current_win then
        vim.api.nvim_set_current_win(win_id)
        vim.g.anya_last_float_ft = "anya-prompt"
        return
      end
    end
  end
end

---Track when leaving a float window so we know if navigation came from inside.
function M.on_float_leave()
  local cur_buf = vim.api.nvim_get_current_buf()
  local ft = vim.api.nvim_buf_get_option(cur_buf, "filetype")
  vim.g.anya_left_float = (ft == "anya-chat" or ft == "anya-prompt")
end

---Handle entering the container window.
---In pane layout:
---  - If we just left a float (anya_left_float), navigate out to the editor.
---  - Otherwise (arriving from the editor), forward into the Anya floats.
---In float layout: always redirect back to the floats.
function M.on_container_enter()
  local left_float = vim.g.anya_left_float or false
  -- Consume the flag immediately so it doesn't linger
  vim.g.anya_left_float = false

  local layout_mode = vim.w.anya_layout_mode
  local layout_direction = vim.w.anya_layout_direction or "right"

  if layout_mode == "pane" then
    if left_float then
      -- Came from a float via <C-h>/<C-l>: navigate out to the editor
      local direction = layout_direction == "left" and "l" or "h"
      local cur = vim.api.nvim_get_current_win()
      pcall(vim.cmd, "wincmd " .. direction)
      if vim.api.nvim_get_current_win() ~= cur then
        return -- Successfully navigated away
      end
      -- No window in that direction; fall through to redirect back into floats
    else
      -- Arrived from the editor: forward focus into the Anya floats
      M.redirect_to_float()
      return
    end
  end

  -- Float layout or fallback: redirect focus back into the floats
  M.redirect_to_float()
end

---Navigate left out of the Anya pane to the editor window.
---Designed for use in pane layout where Anya is a side pane.
---Sets anya_left_float = true so on_container_enter (which fires synchronously
---when we enter the container) knows to navigate out rather than redirect in.
function M.focus_left()
  -- Find the layout (container) window
  local windows = vim.api.nvim_list_wins()
  for _, win_id in ipairs(windows) do
    if vim.api.nvim_win_is_valid(win_id) then
      local buf = vim.api.nvim_win_get_buf(win_id)
      local buf_name = vim.api.nvim_buf_get_name(buf)
      if buf_name:match("Anya Container$") then
        local cfg = vim.api.nvim_win_get_config(win_id)
        -- Only non-floating windows are container windows
        if cfg.relative == "" then
          local cur = vim.api.nvim_get_current_win()
          -- Signal to on_container_enter that we want to navigate out
          vim.g.anya_left_float = true
          -- Enter the container; on_container_enter fires synchronously and
          -- does the wincmd to navigate to the editor.  We just return after.
          vim.api.nvim_set_current_win(win_id)
          -- If on_container_enter couldn't navigate out (no window in that
          -- direction), it will have redirected back to a float already.
          -- Nothing more to do here.
          return
        end
      end
    end
  end
  -- Fallback: no container found, try native wincmd h
  pcall(vim.cmd, "wincmd h")
end

---Check if we should redirect focus back into Anya floats, or navigate out.
---Used by <C-w>h/j/l in the prompt buffer to prevent trapping in the container.
function M.check_and_redirect()
  -- Find the container/layout window to check its layout mode
  local windows = vim.api.nvim_list_wins()
  for _, win_id in ipairs(windows) do
    if vim.api.nvim_win_is_valid(win_id) then
      local buf = vim.api.nvim_win_get_buf(win_id)
      local buf_name = vim.api.nvim_buf_get_name(buf)
      if buf_name:match("Anya Container$") then
        local cfg = vim.api.nvim_win_get_config(win_id)
        if cfg.relative == "" then
          local ok, layout_mode = pcall(vim.api.nvim_win_get_var, win_id, "anya_layout_mode")
          if ok and layout_mode == "pane" then
            M.focus_left()
            return
          end
          break
        end
      end
    end
  end
  -- In non-pane layout, redirect back to float
  M.redirect_to_float()
end


return M
