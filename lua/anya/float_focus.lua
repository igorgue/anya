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

---Focus the chat window
function M.focus_chat()
  local windows = vim.api.nvim_list_wins()
  for _, win_id in ipairs(windows) do
    if vim.api.nvim_win_is_valid(win_id) then
      local buf = vim.api.nvim_win_get_buf(win_id)
      local ft = vim.api.nvim_buf_get_option(buf, "filetype")
      if ft == "anya-chat" then
        vim.api.nvim_set_current_win(win_id)
        vim.g.anya_last_float_ft = "anya-chat"
        return
      end
    end
  end
end

---Focus the prompt window
function M.focus_prompt()
  local windows = vim.api.nvim_list_wins()
  for _, win_id in ipairs(windows) do
    if vim.api.nvim_win_is_valid(win_id) then
      local buf = vim.api.nvim_win_get_buf(win_id)
      local ft = vim.api.nvim_buf_get_option(buf, "filetype")
      if ft == "anya-prompt" then
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
---If we just left a float in pane layout, allow navigation to leave the pane.
---Otherwise, redirect focus back into one of the floats.
function M.on_container_enter()
  local left_float = vim.g.anya_left_float or false
  local layout_mode = vim.w.anya_layout_mode
  local layout_direction = vim.w.anya_layout_direction or "right"

  -- If we're in pane layout and just left a float, allow navigation out.
  -- Perform the wincmd that would have gone to the next window.
  if layout_mode == "pane" and left_float then
    -- For right-side pane, go left; for left-side pane, go right
    local direction = layout_direction == "left" and "l" or "h"
    local cur = vim.api.nvim_get_current_win()
    pcall(vim.cmd, "wincmd " .. direction)
    if vim.api.nvim_get_current_win() ~= cur then
      return -- Successfully navigated away
    end
  end

  -- Otherwise, redirect focus back into one of the floats
  M.redirect_to_float()
end

return M
