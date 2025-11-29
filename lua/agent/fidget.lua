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
		pattern = "AgentRequestFinished",
		group = group,
		callback = function(event)
			local handle = M:pop_progress_handle(event.data.id)
			if handle then
				M:report_exit_status(handle, event)
				handle:finish()
			end
		end,
	})
end

function M:store_progress_handle(id, handle)
	M.handles[id] = handle
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
		message = data.message or "Thinking...",
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
		handle.message = "✓ Completed"
	elseif event.data.status == "error" then
		handle.message = " Error"
	else
		handle.message = "󰜺 Cancelled"
	end
end

return M
