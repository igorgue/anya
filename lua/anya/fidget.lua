local M = {}

-- Check if fidget is available
local has_fidget, fidget_progress = pcall(require, "fidget.progress")

if not has_fidget then
  -- Return a no-op module if fidget is not installed
  function M:init()
    return
  end
  return M
end

M.handles = {}

-- Handle for MCP initialization progress (not tied to a specific request)
M.mcp_handle = nil

-- Handle for conversation title generation
M.title_handle = nil

-- Fun status phrases that will randomly show up
M.status_phrases = {
  -- "brewing",
  -- "cooking",
  -- "crafting",
  -- "analyzing",
  -- "assembling",
  -- "juggling",
  -- "scheming",
  -- "wrangling",
  -- "polishing",
  -- "refining",
  -- "pondering",
  -- "musing",
  -- "orchestrating",
  -- "constructing",
  -- "generating",
  -- "processing",
  -- "deciphering",
  -- "computing",
  -- "synthesizing",
  -- "weaving",
  -- "harmonizing",
  -- "conjuring",
  -- "summoning",
  "generating",
}

-- Store the last message for each handle to restore after tool execution
M.last_messages = {}

function M:get_random_phrase()
  local idx = math.random(1, #M.status_phrases)
  return M.status_phrases[idx]
end

function M:init()
  local group = vim.api.nvim_create_augroup("AnyaFidgetHooks", {})

  vim.api.nvim_create_autocmd({ "User" }, {
    pattern = "AnyaRequestStarted",
    group = group,
    callback = function(event)
      local handle = M:create_progress_handle(event)
      M:store_progress_handle(event.data.id, handle)
    end,
  })

  vim.api.nvim_create_autocmd({ "User" }, {
    pattern = "AnyaRequestFinished",
    group = group,
    callback = function(event)
      local handle = M:get_progress_handle(event.data.id)
      if handle then
        -- Wait for streaming queue to empty before showing completion
        M:wait_for_queue_empty(function()
          if event.data.status == "superseded" then
            M:pop_progress_handle(event.data.id)
            handle:finish()
            return
          end

          M:report_exit_status(handle, event)
          -- Display completion message for at least 1 second
          vim.defer_fn(function()
            M:pop_progress_handle(event.data.id)
            handle:finish()
          end, 1000)
        end)
      end
    end,
  })

  vim.api.nvim_create_autocmd({ "User" }, {
    pattern = "AnyaToolExecution",
    group = group,
    callback = function(event)
      local handle = M:get_progress_handle(event.data.request_id)
      if handle and event.data.tool_name then
        -- Update the message to show the current tool being executed
        handle:report({
          message = event.data.tool_name,
        })
      end
    end,
  })

  vim.api.nvim_create_autocmd({ "User" }, {
    pattern = "AnyaToolExecutionComplete",
    group = group,
    callback = function(event)
      local handle = M:get_progress_handle(event.data.request_id)
      if handle then
        -- Restore the previous message or generate a new random one
        local last_msg = M.last_messages[event.data.request_id]
        if last_msg and last_msg ~= "" then
          handle:report({
            message = last_msg,
          })
        else
          handle:report({
            message = M:get_random_phrase(),
          })
        end
      end
    end,
  })

  -- MCP initialization events (daemon-wide, not tied to a specific request)
  vim.api.nvim_create_autocmd({ "User" }, {
    pattern = "AnyaMemoryStored",
    group = group,
    callback = function(event)
      local handle = M:get_progress_handle(event.data.request_id)
      if handle then
        -- Show memory stored notification for 2 seconds
        local text = event.data.text or ""
        local preview = text:sub(1, 20)
        if #text > 20 then
          preview = preview .. "..."
        end
        handle:report({
          message = string.format("💾 %s", preview),
        })
        -- Store that we're showing a memory notification
        M.memory_notification_active = event.data.request_id
        -- Clear after 2 seconds
        vim.defer_fn(function()
          if M.memory_notification_active == event.data.request_id then
            M.memory_notification_active = nil
          end
        end, 2000)
      end
    end,
  })

  vim.api.nvim_create_autocmd({ "User" }, {
    pattern = "AnyaTitleGenerationStarted",
    group = group,
    callback = function(_event)
      -- Close any existing title handle before creating a new one
      if M.title_handle then
        M.title_handle:finish()
        M.title_handle = nil
      end
      M.title_handle = fidget_progress.handle.create({
        title = "",
        message = "creating conversation title",
        lsp_client = {
          name = " Anya",
        },
      })
    end,
  })

  vim.api.nvim_create_autocmd({ "User" }, {
    pattern = "AnyaTitleGenerationFinished",
    group = group,
    callback = function(event)
      if M.title_handle then
        if event.data.success then
          M.title_handle.message = "title saved"
        else
          M.title_handle.message = "title failed"
        end
        vim.defer_fn(function()
          if M.title_handle then
            M.title_handle:finish()
            M.title_handle = nil
          end
        end, 1000)
      end
    end,
  })

  vim.api.nvim_create_autocmd({ "User" }, {
    pattern = "AnyaMcpInitStarted",
    group = group,
    callback = function(event)
      -- Create a progress handle for MCP initialization
      M.mcp_handle = fidget_progress.handle.create({
        title = "MCP",
        message = event.data.message or "initializing...",
        lsp_client = {
          name = " Anya",
        },
      })
    end,
  })

  vim.api.nvim_create_autocmd({ "User" }, {
    pattern = "AnyaMcpServerUpdate",
    group = group,
    callback = function(event)
      if not M.mcp_handle then
        return
      end
      local server = event.data.server or "unknown"
      local status = event.data.status or "starting"
      if status == "starting" then
        M.mcp_handle.message = string.format("starting %s", server)
      elseif status == "ready" then
        local count = event.data.tool_count or 0
        M.mcp_handle.message = string.format("%s ready (%d tools)", server, count)
      elseif status == "failed" then
        M.mcp_handle.message = string.format("%s failed", server)
      end
    end,
  })

  vim.api.nvim_create_autocmd({ "User" }, {
    pattern = "AnyaMcpInitFinished",
    group = group,
    callback = function(event)
      if M.mcp_handle then
        if event.data.success then
          local server_count = #(event.data.servers or {})
          if server_count > 0 then
            M.mcp_handle.message =
              string.format("connected (%d server%s)", server_count, server_count == 1 and "" or "s")
          else
            M.mcp_handle.message = "no servers configured"
          end
        else
          M.mcp_handle.message = "failed"
        end
        -- Show completion message for 2 seconds before finishing
        vim.defer_fn(function()
          if M.mcp_handle then
            M.mcp_handle:finish()
            M.mcp_handle = nil
          end
        end, 2000)
      end
    end,
  })

  -- :Anya do (headless buffer modification) events

  -- Fired after the buffer has been written; ensure the screen is refreshed
  -- in every window that shows the modified buffer, even if it's not currently
  -- focused or is on a different terminal tab.
  vim.api.nvim_create_autocmd({ "User" }, {
    pattern = "AnyaDoBufferModified",
    group = group,
    callback = function(event)
      local bufnr = event.data and event.data.bufnr
      if not bufnr then
        return
      end
      for _, win in ipairs(vim.api.nvim_list_wins()) do
        if vim.api.nvim_win_get_buf(win) == bufnr then
          vim.api.nvim_win_call(win, function()
            -- Move cursor to top so the user sees the updated content
            vim.cmd("normal! gg")
          end)
        end
      end
    end,
  })

  vim.api.nvim_create_autocmd({ "User" }, {
    pattern = "AnyaDoStarted",
    group = group,
    callback = function(event)
      local handle = fidget_progress.handle.create({
        title = "",
        message = "working",
        lsp_client = {
          name = M:get_model_name(event.data),
        },
      })
      M:store_progress_handle(event.data.id, handle)
    end,
  })

  vim.api.nvim_create_autocmd({ "User" }, {
    pattern = "AnyaDoFinished",
    group = group,
    callback = function(event)
      local handle = M:get_progress_handle(event.data.id)
      if handle then
        if event.data.status == "success" then
          handle.message = "done"
        elseif event.data.status == "superseded" then
          handle.message = nil
        elseif event.data.status == "cancelled" then
          handle.message = "cancelled"
        else
          handle.message = "error"
        end
        M:pop_progress_handle(event.data.id)
        handle:finish()
      end
    end,
  })
end

function M:store_progress_handle(id, handle)
  M.handles[id] = handle
  -- Store the initial message to restore later
  M.last_messages[id] = handle.message
end

function M:get_progress_handle(id)
  return M.handles[id]
end

function M:pop_progress_handle(id)
  local handle = M.handles[id]
  M.handles[id] = nil
  M.last_messages[id] = nil -- Clean up stored message
  return handle
end

function M:create_progress_handle(event)
  -- Support both event objects and direct data tables
  local data = event.data or event
  return fidget_progress.handle.create({
    title = "",
    message = data.message or M:get_random_phrase(),
    lsp_client = {
      name = M:get_model_name(data),
    },
  })
end

function M:get_model_name(data)
  local model = data.model or os.getenv("ANYA_MODEL") or "gpt-4.1"
  return string.format(" %s", model)
end

function M:report_exit_status(handle, event)
  -- If a memory notification is active, show it briefly before final status
  if M.memory_notification_active == event.data.id then
    vim.defer_fn(function()
      if event.data.status == "success" then
        handle.message = "done"
      elseif event.data.status == "error" then
        handle.message = "error"
      elseif event.data.status == "superseded" then
        handle.message = nil
      else
        handle.message = "cancelled"
      end
    end, 2000)
  else
    if event.data.status == "success" then
      handle.message = "done"
    elseif event.data.status == "error" then
      handle.message = "error"
    elseif event.data.status == "superseded" then
      handle.message = nil
    else
      handle.message = "cancelled"
    end
  end
end

-- Wait for the streaming queue to be empty before calling callback
-- Polls every 50ms until queue is empty
function M:wait_for_queue_empty(callback)
  local text = require("anya.text")
  local function check()
    local status = text.get_queue_status()
    if status.queue_length == 0 and not status.timer_running then
      callback()
    else
      vim.defer_fn(check, 50)
    end
  end
  check()
end

return M
