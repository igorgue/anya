local M = {}

-- Namespace for diff highlights and virtual text
local ns_id = vim.api.nvim_create_namespace("agent_diff_view")

-- State constants
local STATE_PENDING = 0
local STATE_ACCEPT = 1
local STATE_REJECT = 2

-- Icons
local ICON_PENDING = "○"
local ICON_APPLIED = ""
local ICON_REJECTED = ""

-- Colors for virtual text
local HL_ACCEPT = "String"      -- Green
local HL_REJECT = "ErrorMsg"    -- Red
local HL_PENDING = "Comment"    -- Grey

-- Store patch data by extmark id
local patch_registry = {}

--- Parse diff stats from patch content
local function parse_diff_stats(content)
    local additions = 0
    local deletions = 0
    local modifications = 0
    
    for _, line in ipairs(vim.split(content, "\n")) do
        if line:match("^%+") and not line:match("^%+%+%+") then
            additions = additions + 1
        elseif line:match("^%-") and not line:match("^%-%-%-") then
            deletions = deletions + 1
        end
    end
    
    return additions, modifications, deletions
end

--- Parse filename from patch content
local function parse_filename(content)
    -- Try diff --git header first
    local name = content:match("diff %-%-git a/([%S]+)")
    if not name then
        -- Try +++ header
        name = content:match("%+%+%+ b/([%S]+)")
    end
    if not name then
        -- Try --- header
        name = content:match("%-%-%- a/([%S]+)")
    end
    return name or "unknown file"
end

--- Get virtual text for the header based on state
local function get_header_virt_text(state, additions, modifications, deletions)
    local virt_text = {}
    
    -- Status Icon
    local icon = ICON_PENDING
    local icon_hl = HL_PENDING
    
    if state == STATE_ACCEPT then
        icon = ICON_APPLIED
        icon_hl = HL_ACCEPT
    elseif state == STATE_REJECT then
        icon = ICON_REJECTED
        icon_hl = HL_REJECT
    end
    
    table.insert(virt_text, { icon .. "  ", icon_hl })
    
    -- Controls
    local function add_option(opt_state, label, key)
        local hl = "Comment"
        if state == opt_state then
            if state == STATE_ACCEPT then hl = "String"
            elseif state == STATE_REJECT then hl = "ErrorMsg"
            end
            -- Highlight the active option
            table.insert(virt_text, { string.format("[%s: %s]", key, label), hl })
        else
            table.insert(virt_text, { string.format("%s: %s", key, label), "Comment" })
        end
        table.insert(virt_text, { " | ", "Comment" })
    end
    
    add_option(STATE_ACCEPT, "apply", "1")
    table.remove(virt_text) -- Remove trailing pipe
    table.insert(virt_text, { " | ", "Comment" })
    add_option(STATE_REJECT, "reject", "2")
    table.remove(virt_text) -- Remove last pipe
    
    return virt_text
end

--- Update the header line text (Stats) and virtual text (Controls)
local function update_patch_header(bufnr, extmark_id)
    local patch_data = patch_registry[extmark_id]
    if not patch_data then return end
    
    local extmark = vim.api.nvim_buf_get_extmark_by_id(bufnr, ns_id, extmark_id, { details = true })
    if not extmark or #extmark == 0 then return end
    
    local row = extmark[1]
    
    local adds, mods, dels = parse_diff_stats(patch_data.content)
    local filename = parse_filename(patch_data.content)
    
    -- Update the header line text with stats and filename
    local stats_line = string.format("+%d ~%d -%d | %s", adds, mods, dels, filename)
    
    -- Optimization: Only update line if it changed (prevents fold flickering)
    local current_line = vim.api.nvim_buf_get_lines(bufnr, row, row + 1, false)[1]
    if current_line ~= stats_line then
        vim.api.nvim_buf_set_lines(bufnr, row, row + 1, false, { stats_line })
    end
    
    -- Update virtual text (Controls)
    local virt_text = get_header_virt_text(patch_data.state, adds, mods, dels)
    
    -- Calculate current end_row based on original height
    -- This ensures the range is correct even if the block moved
    local height = patch_data.end_row - patch_data.header_row
    local current_end_row = row + height
    
    vim.api.nvim_buf_set_extmark(bufnr, ns_id, row, 0, {
        id = extmark_id,
        virt_text = virt_text,
        virt_text_pos = "right_align",
        end_row = current_end_row,
    })
    
    -- Always re-apply highlights after extmark update (extmarks can override inline highlights)
    local line = stats_line
    local s_add, e_add = line:find("%+%d+")
    if s_add then vim.api.nvim_buf_add_highlight(bufnr, ns_id, "OkMsg", row, s_add - 1, e_add) end
    
    local s_mod, e_mod = line:find("~%d+")
    if s_mod then vim.api.nvim_buf_add_highlight(bufnr, ns_id, "WarningMsg", row, s_mod - 1, e_mod) end
    
    local s_del, e_del = line:find("%-%d+")
    if s_del then vim.api.nvim_buf_add_highlight(bufnr, ns_id, "ErrorMsg", row, s_del - 1, e_del) end
    
    -- Highlight filename
    local s_file = line:find("| (.+)")
    if s_file then
         vim.api.nvim_buf_add_highlight(bufnr, ns_id, "Directory", row, s_file + 1, -1)
    end
end

--- Toggle state for the patch under cursor
function M.handle_keypress(bufnr, key)
    local cursor = vim.api.nvim_win_get_cursor(0)
    local row = cursor[1] - 1
    
    local extmarks = vim.api.nvim_buf_get_extmarks(bufnr, ns_id, 0, -1, { details = true })
    
    for _, mark in ipairs(extmarks) do
        local id = mark[1]
        
        -- Only process extmarks that are in our registry (headers)
        -- This filters out highlight extmarks which might cause nil errors
        if patch_registry[id] then
            local start_row = mark[2]
            local end_row = mark[4].end_row
            
            if end_row and row >= start_row and row <= end_row then
            local current_state = patch_registry[id].state
            local new_state
            if key == "1" then new_state = STATE_ACCEPT
            elseif key == "2" then new_state = STATE_REJECT
            else return end
            
            -- If state hasn't changed, do nothing
            if current_state == new_state then
                return
            end
            
            -- Update state in registry and UI
            patch_registry[id].state = new_state
            update_patch_header(bufnr, id)
            
            -- Handle actions based on transition
            if new_state == STATE_ACCEPT then
                -- Moving TO accept: Always apply
                -- (If we were PENDING or REJECT, we need to apply)
                vim.fn.AgentPatchAction("apply", patch_registry[id].content)
            elseif new_state == STATE_REJECT then
                -- Moving TO reject:
                -- Only reverse if we were previously ACCEPTED
                if current_state == STATE_ACCEPT then
                    vim.fn.AgentPatchAction("reject", patch_registry[id].content)
                end
                -- If we were PENDING, we just mark as rejected in UI, no git action needed
            end
            return
            end
        end
    end
end

--- Render a diff block in the buffer
function M.render_diff(bufnr, content)
    if not content:match("\n$") then content = content .. "\n" end
    local lines = vim.split(content, "\n")
    if lines[#lines] == "" then table.remove(lines) end
    
    local start_line = vim.api.nvim_buf_line_count(bufnr)
    
    -- Calculate stats for header
    local adds, mods, dels = parse_diff_stats(content)
    local filename = parse_filename(content)
    local header_text = string.format("+%d ~%d -%d | %s", adds, mods, dels, filename)
    
    local block_lines = {}
    table.insert(block_lines, header_text)
    table.insert(block_lines, "```diff")
    for _, line in ipairs(lines) do
        table.insert(block_lines, line)
    end
    table.insert(block_lines, "```")
    table.insert(block_lines, "")
    
    vim.api.nvim_buf_set_lines(bufnr, start_line, -1, false, block_lines)
    
    -- Apply diff highlighting to the code block lines manually
    -- Treesitter injection doesn't always work for dynamically added content
    local diff_start = start_line + 2  -- After header and ```diff
    local diff_end = start_line + #block_lines - 2  -- Before closing ```
    
    for i = diff_start, diff_end do
        local line = vim.api.nvim_buf_get_lines(bufnr, i, i + 1, false)[1] or ""
        if line:match("^diff %-%-git") then
            vim.api.nvim_buf_add_highlight(bufnr, ns_id, "Statement", i, 0, -1)
        elseif line:match("^index ") then
            vim.api.nvim_buf_add_highlight(bufnr, ns_id, "Comment", i, 0, -1)
        elseif line:match("^%-%-%- ") then
            vim.api.nvim_buf_add_highlight(bufnr, ns_id, "DiffDelete", i, 0, -1)
        elseif line:match("^%+%+%+ ") then
            vim.api.nvim_buf_add_highlight(bufnr, ns_id, "DiffAdd", i, 0, -1)
        elseif line:match("^@@") then
            vim.api.nvim_buf_add_highlight(bufnr, ns_id, "Function", i, 0, -1)
        elseif line:match("^%+") then
            vim.api.nvim_buf_add_highlight(bufnr, ns_id, "DiffAdd", i, 0, -1)
        elseif line:match("^%-") then
            vim.api.nvim_buf_add_highlight(bufnr, ns_id, "DiffDelete", i, 0, -1)
        end
    end
    
    local header_row = start_line
    local end_row = start_line + #block_lines - 1
    
    -- Create extmark
    local initial_state = STATE_PENDING -- Default to pending (gray)
    local virt_text = get_header_virt_text(initial_state, adds, mods, dels)
    
    local id = vim.api.nvim_buf_set_extmark(bufnr, ns_id, header_row, 0, {
        virt_text = virt_text,
        virt_text_pos = "right_align",
        end_row = end_row,
        hl_group = "Normal",
    })
    
    patch_registry[id] = {
        state = initial_state,
        content = content,
        header_row = header_row,
        end_row = end_row
    }
    
    -- Initial highlight for stats
    local line = header_text
    local s_add, e_add = line:find("%+%d+")
    if s_add then vim.api.nvim_buf_add_highlight(bufnr, ns_id, "OkMsg", header_row, s_add - 1, e_add) end
    local s_mod, e_mod = line:find("~%d+")
    if s_mod then vim.api.nvim_buf_add_highlight(bufnr, ns_id, "WarningMsg", header_row, s_mod - 1, e_mod) end
    local s_del, e_del = line:find("%-%d+")
    if s_del then vim.api.nvim_buf_add_highlight(bufnr, ns_id, "ErrorMsg", header_row, s_del - 1, e_del) end
    
    -- Highlight filename
    local s_file = line:find("| (.+)")
    if s_file then
         -- Highlight the filename part (after the pipe)
         vim.api.nvim_buf_add_highlight(bufnr, ns_id, "Directory", header_row, s_file + 1, -1)
    end
    
    pcall(function()
        require('agent_nvim.folds').create_fold(bufnr, start_line + 1, end_row + 1)
    end)
    
    M.setup_keymaps(bufnr)
    return id
end

function M.setup_keymaps(bufnr)
    local opts = { noremap = true, silent = true, buffer = bufnr }
    vim.keymap.set("n", "1", function() M.handle_keypress(bufnr, "1") end, opts)
    vim.keymap.set("n", "2", function() M.handle_keypress(bufnr, "2") end, opts)
end

function M.get_patches(bufnr)
    local patches = {}
    local extmarks = vim.api.nvim_buf_get_extmarks(bufnr, ns_id, 0, -1, { details = true })
    
    for _, mark in ipairs(extmarks) do
        local id = mark[1]
        local data = patch_registry[id]
        if data then
            local current_row = mark[2]
            local end_row = mark[4].end_row
            local content_start = current_row + 2
            local content_end = end_row - 1
            
            if content_start <= content_end then
                local lines = vim.api.nvim_buf_get_lines(bufnr, content_start, content_end, false)
                local content = table.concat(lines, "\n")
                table.insert(patches, { content = content, state = data.state })
            end
        end
    end
    return patches
end

return M
