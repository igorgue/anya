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

-- Fun status phrases that will randomly show up
M.status_phrases = {
  "brewing",
  "cooking",
  "crafting",
  "analyzing",
  "assembling",
  "juggling",
  "scheming",
  "wrangling",
  "polishing",
  "refining",
  "pondering",
  "musing",
  "orchestrating",
  "constructing",
  "generating",
  "processing",
  "deciphering",
  "computing",
  "synthesizing",
  "weaving",
  "harmonizing",
  "conjuring",
  "summoning",
}

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
    message = data.message or M:get_random_phrase(),
    lsp_client = {
      name = M:get_model_name(data),
    },
  })
end

function M:get_model_name(data)
  local model = data.model or os.getenv("ANYA_MODEL") or "gpt-4.1"
  return string.format(" Anya (%s)", model)
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
