local files = {}
local blink_utils = require('agent_nvim.blink.init')

-- Helper function to find @ symbol and return the path after it
local function get_at_symbol_path(line, cursor_col)
  -- Search from cursor position backwards for @ symbol
  local at_pos = nil

  for i = cursor_col, 1, -1 do
    local char = line:sub(i, i)
    if char == '@' then
      at_pos = i
      break
    elseif char == ' ' then
      -- Stop at space to find the previous command/mention
      break
    end
  end

  if not at_pos then
    return nil, nil
  end

  -- Extract the path after @
  local path_after_at = line:sub(at_pos + 1, cursor_col)
  return at_pos, path_after_at
end

-- Helper function to find the start of the basename after the last slash
local function get_basename_start(path, cursor_offset)
  -- Find the last slash before the cursor position
  local last_slash_pos = nil
  for i = cursor_offset, 1, -1 do
    if path:sub(i, i) == '/' then
      last_slash_pos = i
      break
    end
  end

  if last_slash_pos then
    -- Start after the last slash
    return last_slash_pos + 1
  else
    -- No slash found, start from the beginning
    return 1
  end
end

-- Helper function to get directory and basename from path
local function parse_path(path)
  if path == '' then
    return '', ''
  end

  -- Find the last slash to separate directory from basename
  local last_slash = path:find('/[^/]*$')

  if last_slash then
    local dirname = path:sub(1, last_slash - 1)
    local basename = path:sub(last_slash + 1)
    return dirname, basename
  else
    -- No directory component, just basename
    return '', path
  end
end

-- Helper function to resolve relative path to absolute
local function resolve_path(path)
  if path == '' then
    return vim.fn.getcwd()
  end

  -- If already absolute, return as is
  if path:sub(1, 1) == '/' then
    return path
  end

  -- Otherwise, resolve relative to cwd
  return vim.fn.resolve(vim.fn.getcwd() .. '/' .. path)
end

function files.new(opts)
  return {
    -- Check if this source should be enabled for the current buffer
    enabled = function()
      local ft = vim.bo.filetype
      return ft == 'agent-prompt'
    end,

    get_trigger_characters = function()
      return { '@' }
    end,

    get_completions = function(self, ctx, callback)
      local line = vim.api.nvim_buf_get_lines(ctx.bufnr, ctx.cursor[1] - 1, ctx.cursor[1], false)[1]
      local cursor_col = ctx.cursor[2] -- cursor is {line, col} in 1-indexed format

      -- Find @ symbol and get path after it
      local at_pos, path_after_at = get_at_symbol_path(line, cursor_col)

      if not at_pos then
        callback({
          items = {},
          is_incomplete_backward = false,
          is_incomplete_forward = false
        })
        return
      end

      -- Parse the path into directory and basename components
      local dirname, basename = parse_path(path_after_at)

      -- Resolve the directory to scan
      local scan_dir = resolve_path(dirname)

      -- Calculate where the basename starts for proper textEdit range
      local path_length = cursor_col - at_pos -- Length of path after @
      local basename_start_offset = get_basename_start(path_after_at, path_length)
      local replace_start_pos = at_pos + basename_start_offset - 1 -- Convert to 0-indexed for textEdit

      local items = {}
      local uv = vim.loop

      -- Scan the directory
      local handle = uv.fs_scandir(scan_dir)
      if handle then
        while true do
          local name, type = uv.fs_scandir_next(handle)
          if not name then
            break
          end

          -- Skip hidden files and .git
          if name:sub(1, 1) ~= '.' and name ~= '.git' then
            -- Check if name matches the basename prefix (case insensitive)
            if basename == '' or name:lower():find(basename:lower(), 1, true) == 1 then
              local is_dir = type == 'directory'
              local label = is_dir and (name .. '/') or name
              local kind = is_dir and 19 or 17 -- 19 = Folder, 17 = File

              -- For textEdit, we only want to replace the basename, not include directory
              -- The directory part is already preserved in the text
              table.insert(items, {
                label = label,
                kind = vim.lsp and vim.lsp.CompletionItemKind and kind or kind,
                insertText = dirname .. label,
                textEdit = {
                  newText = label, -- Only insert the filename, not directory
                  range = {
                    start = {
                      line = ctx.cursor[1] - 1, -- 0-indexed line
                      character = replace_start_pos -- Start replacing at basename
                    },
                    ['end'] = {
                      line = ctx.cursor[1] - 1, -- 0-indexed line
                      character = cursor_col -- Cursor position
                    }
                  }
                }
              })
            end
          end
        end
      end

      -- Sort: directories first, then files, alphabetically
      table.sort(items, function(a, b)
        local a_is_dir = a.label:sub(-1) == '/'
        local b_is_dir = b.label:sub(-1) == '/'
        if a_is_dir and not b_is_dir then
          return true
        elseif not a_is_dir and b_is_dir then
          return false
        else
          return a.label:lower() < b.label:lower()
        end
      end)

      callback({
        items = items,
        is_incomplete_backward = false,
        is_incomplete_forward = false
      })

      -- Return cancellation function
      return function()
        -- Cancel any pending async operations if needed
      end
    end
  }
end

return files