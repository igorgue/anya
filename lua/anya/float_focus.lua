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
        return
      end
    end
  end
end

---Handle entering the container window
---Acts as a router:
--- - From outside: Focus Prompt
--- - From inside (Chat/Prompt): Bounce out (Left)
function M.on_container_enter()
  -- Identify windows
  local chat_win = nil
  local prompt_win = nil
  local windows = vim.api.nvim_list_wins()
  
  for _, win_id in ipairs(windows) do
    if vim.api.nvim_win_is_valid(win_id) then
      local buf = vim.api.nvim_win_get_buf(win_id)
      local ft = vim.api.nvim_buf_get_option(buf, "filetype")
      if ft == "anya-chat" then
        chat_win = win_id
      elseif ft == "anya-prompt" then
        prompt_win = win_id
      end
    end
  end

  local prev_win = vim.fn.win_getid(vim.fn.winnr('#'))
  
  -- Check if we came from one of our floats
  if (prompt_win and prev_win == prompt_win) or (chat_win and prev_win == chat_win) then
    -- Came from inside, user wants to leave. Bounce Left.
    -- TODO: Determine direction dynamically if layout supports left-side pane
    vim.cmd("wincmd h")
  else
    -- Came from outside (or unknown), focus Prompt
    if prompt_win and vim.api.nvim_win_is_valid(prompt_win) then
      vim.api.nvim_set_current_win(prompt_win)
    end
  end
end

return M
