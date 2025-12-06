local files = {}

-- Helper function to find @ symbol and return the query after it
local function get_at_symbol_query(line, cursor_col)
  local at_pos = nil

  for i = cursor_col, 1, -1 do
    local char = line:sub(i, i)
    if char == "@" then
      at_pos = i
      break
    elseif char == " " then
      break
    end
  end

  if not at_pos then
    return nil, nil
  end

  local query = line:sub(at_pos + 1, cursor_col)
  return at_pos, query
end

-- Simple fuzzy match: check if all characters in pattern appear in str in order
local function fuzzy_match(str, pattern)
  if pattern == "" then
    return true, 0
  end

  local str_lower = str:lower()
  local pattern_lower = pattern:lower()
  local str_idx = 1
  local score = 0
  local consecutive = 0
  local last_match_idx = 0

  for i = 1, #pattern_lower do
    local char = pattern_lower:sub(i, i)
    local found = false

    while str_idx <= #str_lower do
      if str_lower:sub(str_idx, str_idx) == char then
        found = true
        -- Bonus for consecutive matches
        if str_idx == last_match_idx + 1 then
          consecutive = consecutive + 1
          score = score + consecutive * 2
        else
          consecutive = 0
        end
        -- Bonus for matching at start or after separator
        if str_idx == 1 or str:sub(str_idx - 1, str_idx - 1):match("[/_.-]") then
          score = score + 5
        end
        last_match_idx = str_idx
        str_idx = str_idx + 1
        break
      end
      str_idx = str_idx + 1
    end

    if not found then
      return false, 0
    end
  end

  -- Prefer shorter paths (penalty for length)
  score = score - (#str * 0.1)
  -- Prefer matches where pattern is a larger portion of the filename
  local basename = str:match("[^/]+$") or str
  score = score + (#pattern / #basename) * 10

  return true, score
end

-- Get project root (look for .git, fallback to cwd)
local function get_project_root()
  local cwd = vim.fn.getcwd()
  local path = cwd

  while path ~= "/" do
    if vim.fn.isdirectory(path .. "/.git") == 1 then
      return path
    end
    path = vim.fn.fnamemodify(path, ":h")
  end

  return cwd
end

-- Collect files using fd if available, fallback to Lua implementation
local function collect_files(root, collected, limit)
  -- Try to use fd first (much faster and respects gitignore)
  local fd_cmd = "fd -tf --type f --hidden --exclude .git . " .. vim.fn.shellescape(root)
  local handle = io.popen(fd_cmd)

  if handle then
    for line in handle:lines() do
      if #collected >= limit then
        break
      end
      -- Remove root path and leading slash
      local relative_path = line:sub(#root + 2)
      table.insert(collected, relative_path)
    end
    handle:close()

    -- If we found files with fd, we're done
    if #collected > 0 then
      return
    end
  end

  -- Fallback to Lua implementation
  local uv = vim.loop
  local dir_handle = uv.fs_scandir(root)

  if not dir_handle then
    return
  end

  local function collect_recursive(dir)
    local handle = uv.fs_scandir(dir)
    if not handle then
      return
    end

    while #collected < limit do
      local name, type = uv.fs_scandir_next(handle)
      if not name then
        break
      end

      -- Skip hidden files and common ignore patterns
      if name:sub(1, 1) ~= "."
         and name ~= "node_modules"
         and name ~= "__pycache__"
         and name ~= "target"
         and name ~= "build"
         and name ~= "dist"
         and name ~= "venv"
         and name ~= ".venv"
         and name ~= "vendor" then
        local full_path = dir .. "/" .. name
        local relative_path = full_path:sub(#root + 2)

        if type == "directory" then
          collect_recursive(full_path)
        else
          table.insert(collected, relative_path)
        end
      end
    end
  end

  collect_recursive(root)
end

function files.new(opts)
  -- Cache for project files
  local file_cache = nil
  local cache_root = nil

  return {
    enabled = function()
      return vim.bo.filetype == "anya-prompt"
    end,

    get_trigger_characters = function()
      return { "@" }
    end,

    get_completions = function(self, ctx, callback)
      local line = vim.api.nvim_buf_get_lines(ctx.bufnr, ctx.cursor[1] - 1, ctx.cursor[1], false)[1]
      local cursor_col = ctx.cursor[2]

      local at_pos, query = get_at_symbol_query(line, cursor_col)

      if not at_pos then
        callback({
          items = {},
          is_incomplete_backward = false,
          is_incomplete_forward = false,
        })
        return
      end

      local project_root = get_project_root()

      -- Refresh cache if project root changed
      if file_cache == nil or cache_root ~= project_root then
        file_cache = {}
        cache_root = project_root
        collect_files(project_root, file_cache, 5000)
      end

      local items = {}
      local scored_items = {}

      for _, file_path in ipairs(file_cache) do
        local matches, score = fuzzy_match(file_path, query)
        if matches then
          table.insert(scored_items, { path = file_path, score = score })
        end
      end

      -- Sort by score descending
      table.sort(scored_items, function(a, b)
        return a.score > b.score
      end)

      -- Limit results
      local max_results = 50
      for i = 1, math.min(#scored_items, max_results) do
        local file_path = scored_items[i].path
        table.insert(items, {
          label = file_path,
          kind = 17, -- File
          insertText = file_path,
          textEdit = {
            newText = file_path,
            range = {
              start = {
                line = ctx.cursor[1] - 1,
                character = at_pos, -- Replace everything after @
              },
              ["end"] = {
                line = ctx.cursor[1] - 1,
                character = cursor_col,
              },
            },
          },
        })
      end

      callback({
        items = items,
        is_incomplete_backward = false,
        is_incomplete_forward = false,
      })

      return function() end
    end,
  }
end

return files
