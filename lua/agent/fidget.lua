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

function M:init()
  local group = vim.api.nvim_create_augroup("AgentNvimFidgetHooks", {})

  vim.api.nvim_create_autocmd({ "User" }, {
    pattern = "AgentRequestStarted",
    group = group,
    callback = function(event)
      local handle = M:create_progress_handle(event)
      M:store_progress_handle(event.data.id, handle)
    end,
  })

  vim.api.nvim_create_autocmd({ "User" }, {
    pattern = "AgentToolCall",
    group = group,
    callback = function(event)
      local handle = M:get_progress_handle(event.data.id)
      if handle then
        handle.message = event.data.message or "calling tool"
      end
    end,
  })

  vim.api.nvim_create_autocmd({ "User" }, {
    pattern = "AgentToolResult",
    group = group,
    callback = function(event)
      local handle = M:get_progress_handle(event.data.id)
      if handle then
        handle.message = event.data.message or "processing result"
      end
    end,
  })

  vim.api.nvim_create_autocmd({ "User" }, {
    pattern = "AgentCancelling",
    group = group,
    callback = function(event)
      local handle = M:get_progress_handle(event.data.id)
      if handle then
        handle.message = "cancelling"
      end
    end,
  })

  vim.api.nvim_create_autocmd({ "User" }, {
    pattern = "AgentRequestFinished",
    group = group,
    callback = function(event)
      local handle = M:get_progress_handle(event.data.id)
      if handle then
        handle.message = "cancelling"
      end
    end,
  })

  vim.api.nvim_create_autocmd({ "User" }, {
    pattern = "AgentRequestFinished",
    group = group,
    callback = function(event)
      local handle = M:get_progress_handle(event.data.id)
      if handle then
        M:report_exit_status(handle, event)
        -- Display completion message for at least 1 second
        vim.defer_fn(function()
          M:pop_progress_handle(event.data.id)
          handle:finish()
        end, 1000)
      end
    end,
  })
end

function M:store_progress_handle(id, handle)
  M.handles[id] = handle
end

function M:get_progress_handle(id)
  return M.handles[id]
end

function M:pop_progress_handle(id)
  local handle = M.handles[id]
  M.handles[id] = nil
  return handle
end

function M:create_progress_handle(event)
  -- Support both event objects and direct data tables
  local data = event.data or event
  return fidget_progress.handle.create({
    title = "",
    message = data.message or "thinking",
    lsp_client = {
      name = M:get_model_name(data),
    },
  })
end

function M:get_model_name(data)
  local model = data.model or os.getenv("AGENT_MODEL") or "gpt-4o"
  return string.format(" Agent (%s)", model)
end

function M:report_exit_status(handle, event)
  if event.data.status == "success" then
    handle.message = "✓ completed"
  elseif event.data.status == "error" then
    handle.message = " error"
  else
    handle.message = "󰜺 cancelled"
  end
end

return M
