local M = {}

-- Namespace for diff highlights and virtual text
local ns_id = vim.api.nvim_create_namespace("agent_diff_view")

-- State constants
local STATE_ALWAYS_ACCEPT = 0
local STATE_ACCEPT = 1
local STATE_REJECT = 2
local STATE_YOLO = 0 -- Alias for always accept

-- Colors for virtual text
-- We'll use existing highlight groups or define new ones linked to them
local HL_ACCEPT = "String"      -- Green-ish usually
local HL_REJECT = "ErrorMsg"    -- Red
local HL_NEUTRAL = "Comment"    -- Grey
local HL_HEADER = "Title"

-- Store patch data by extmark id
-- key: extmark_id, value: { state = STATE_ACCEPT, content = "...", original_header_line = ... }
local patch_registry = {}

--- Parse diff stats from patch content
--- @param content string
--- @return number, number, number (additions, modifications, deletions)
local function parse_diff_stats(content)
    local additions = 0
    local deletions = 0
    local modifications = 0 -- Git apply usually just shows add/del, but we can infer ~ from context if we want, 
                            -- but standard git diff is usually just + and - lines.
                            -- For simplicity, we'll count lines starting with + as add, - as del.
                            -- 'Modifications' is harder to track without context, so we might just stick to + and - for now
                            -- unless we want to get fancy with hunk analysis.
                            -- Let's stick to + and - for now as it's reliable.
    
    for _, line in ipairs(vim.split(content, "\n")) do
        if line:match("^%+") and not line:match("^%+%+%+") then
            additions = additions + 1
        elseif line:match("^%-") and not line:match("^%-%-%-") then
            deletions = deletions + 1
        end
    end
    
    return additions, modifications, deletions
end

--- Get virtual text for the header based on state
--- @param state number
--- @param additions number
--- @param modifications number
--- @param deletions number
--- @return table
local function get_header_virt_text(state, additions, modifications, deletions)
    local virt_text = {}
    
    -- Stats part (Left side or Right side? Request said Right side for stats, Left side for controls? 
    -- Request: "On the right side it should have something like this: +19 ~3 -9 on the left side it should have the `0: yolo | 1: accept | 2: reject`"
    -- Wait, the example showed:
    -- +19 ~3 -9                                                         `0: yolo | 1: accept | 2: reject`
    -- Let's follow the example visual: Stats on left, Controls on right.
    
    -- Stats
    table.insert(virt_text, { string.format("+%d ", additions), "OkMsg" })
    if modifications > 0 then
        table.insert(virt_text, { string.format("~%d ", modifications), "WarningMsg" })
    end
    table.insert(virt_text, { string.format("-%d ", deletions), "ErrorMsg" })
    
    -- Spacer
    table.insert(virt_text, { "   ", "Normal" })
    
    -- Controls
    local function add_option(opt_state, label, key)
        local hl = "Comment"
        if state == opt_state then
            if state == STATE_ACCEPT then hl = "String" -- Green
            elseif state == STATE_REJECT then hl = "ErrorMsg" -- Red
            elseif state == STATE_ALWAYS_ACCEPT then hl = "Special" -- Purple/Blue
            end
            table.insert(virt_text, { string.format("[%s: %s]", key, label), hl })
        else
            table.insert(virt_text, { string.format("%s: %s", key, label), "Comment" })
        end
        table.insert(virt_text, { " | ", "Comment" })
    end
    
    add_option(STATE_ALWAYS_ACCEPT, "always accept", "0")
    add_option(STATE_ACCEPT, "accept", "1")
    -- Remove trailing pipe
    table.remove(virt_text) 
    table.insert(virt_text, { " | ", "Comment" })
    add_option(STATE_REJECT, "reject", "2")
    table.remove(virt_text) -- Remove last pipe
    
    return virt_text
end

--- Update the virtual text for a specific patch block
--- @param bufnr number
--- @param extmark_id number
local function update_patch_header(bufnr, extmark_id)
    local patch_data = patch_registry[extmark_id]
    if not patch_data then return end
    
    local extmark = vim.api.nvim_buf_get_extmark_by_id(bufnr, ns_id, extmark_id, { details = true })
    if not extmark or #extmark == 0 then return end
    
    local row = extmark[1]
    
    local adds, mods, dels = parse_diff_stats(patch_data.content)
    local virt_text = get_header_virt_text(patch_data.state, adds, mods, dels)
    
    -- Update extmark with new virtual text
    vim.api.nvim_buf_set_extmark(bufnr, ns_id, row, 0, {
        id = extmark_id,
        virt_text = virt_text,
        virt_text_pos = "right_align",
        hl_group = "Normal",
    })
end

--- Toggle state for the patch under cursor
--- @param bufnr number
--- @param key string "0", "1", or "2"
function M.handle_keypress(bufnr, key)
    local cursor = vim.api.nvim_win_get_cursor(0)
    local row = cursor[1] - 1 -- 0-indexed
    
    -- Find which patch block we are in
    -- We can search for extmarks in the buffer
    local extmarks = vim.api.nvim_buf_get_extmarks(bufnr, ns_id, 0, -1, { details = true })
    
    for _, mark in ipairs(extmarks) do
        local id = mark[1]
        local start_row = mark[2]
        local end_row = mark[4].end_row
        
        if row >= start_row and row <= end_row then
            -- Found the block
            local new_state
            if key == "0" then new_state = STATE_ALWAYS_ACCEPT
            elseif key == "1" then new_state = STATE_ACCEPT
            elseif key == "2" then new_state = STATE_REJECT
            else return end
            
            if patch_registry[id] then
                patch_registry[id].state = new_state
                update_patch_header(bufnr, id)
                print("Patch state updated: " .. (new_state == 2 and "Reject" or "Accept"))
            end
            return
        end
    end
end

--- Render a diff block in the buffer
--- @param bufnr number
--- @param content string The patch content
function M.render_diff(bufnr, content)
    -- Ensure content ends with newline
    if not content:match("\n$") then
        content = content .. "\n"
    end
    
    local lines = vim.split(content, "\n")
    -- Remove last empty string from split if content ended with newline
    if lines[#lines] == "" then table.remove(lines) end
    
    -- Get current line count to append to end
    local start_line = vim.api.nvim_buf_line_count(bufnr)
    
    -- Prepare lines to append
    -- We wrap the diff in a code block for syntax highlighting, 
    -- BUT the requirement says "The whole block is a fold too".
    -- And we need virtual text on top.
    
    -- Let's insert a header line (empty or specific marker) that will hold the virtual text
    -- Then the diff content.
    
    local block_lines = {}
    table.insert(block_lines, "") -- Header line for virtual text
    table.insert(block_lines, "```diff")
    for _, line in ipairs(lines) do
        table.insert(block_lines, line)
    end
    table.insert(block_lines, "```")
    table.insert(block_lines, "") -- Spacer
    
    -- Append lines
    vim.api.nvim_buf_set_lines(bufnr, start_line, -1, false, block_lines)
    
    -- Calculate range
    local header_row = start_line
    local end_row = start_line + #block_lines - 1
    
    -- Create extmark for the whole block
    local adds, mods, dels = parse_diff_stats(content)
    local initial_state = STATE_ACCEPT -- Default to accept
    
    local virt_text = get_header_virt_text(initial_state, adds, mods, dels)
    
    local id = vim.api.nvim_buf_set_extmark(bufnr, ns_id, header_row, 0, {
        virt_text = virt_text,
        virt_text_pos = "right_align",
        end_row = end_row,
        hl_group = "Normal",
    })
    
    -- Register patch data
    patch_registry[id] = {
        state = initial_state,
        content = content,
        header_row = header_row,
        end_row = end_row
    }
    
    -- Create fold
    -- We need to ensure foldmethod is manual or expr that respects this
    -- For now, let's try to use the `create_fold` from folds.lua if available, or manual fold
    -- The requirement says "The whole block is a fold too and by default it should be open"
    
    -- We'll try to use the existing fold mechanism in agent.nvim if possible
    -- require('agent_nvim.folds').create_fold(bufnr, start_line + 1, end_row + 1)
    -- Note: start_line is 0-indexed, create_fold expects 1-indexed
    
    pcall(function()
        require('agent_nvim.folds').create_fold(bufnr, start_line + 1, end_row + 1)
    end)
    
    -- Setup keymaps if not already set
    M.setup_keymaps(bufnr)
    
    return id
end

--- Setup keymaps for the buffer
--- @param bufnr number
function M.setup_keymaps(bufnr)
    local opts = { noremap = true, silent = true, buffer = bufnr }
    vim.keymap.set("n", "0", function() M.handle_keypress(bufnr, "0") end, opts)
    vim.keymap.set("n", "1", function() M.handle_keypress(bufnr, "1") end, opts)
    vim.keymap.set("n", "2", function() M.handle_keypress(bufnr, "2") end, opts)
end

--- Get all patches and their states
--- @param bufnr number
--- @return table List of { content: string, state: number }
function M.get_patches(bufnr)
    local patches = {}
    local extmarks = vim.api.nvim_buf_get_extmarks(bufnr, ns_id, 0, -1, { details = true })
    
    for _, mark in ipairs(extmarks) do
        local id = mark[1]
        local data = patch_registry[id]
        if data then
            -- If the user edited the buffer, we should try to retrieve the updated content
            -- The content is between header_row + 2 (skip header and ```diff) and end_row - 1 (skip ```)
            -- But rows might have shifted if user edited outside.
            -- Using extmark position is safer.
            
            local current_row = mark[2]
            local end_row = mark[4].end_row
            
            -- Assuming the structure is preserved:
            -- Row 0: Header (Virtual Text)
            -- Row 1: ```diff
            -- Row 2...N: Content
            -- Row N+1: ```
            
            local content_start = current_row + 2
            local content_end = end_row - 1
            
            if content_start <= content_end then
                local lines = vim.api.nvim_buf_get_lines(bufnr, content_start, content_end, false)
                local content = table.concat(lines, "\n")
                
                table.insert(patches, {
                    content = content,
                    state = data.state
                })
            end
        end
    end
    
    return patches
end

return M
