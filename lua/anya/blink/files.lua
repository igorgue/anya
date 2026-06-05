local files = {}

-- Helper function to find @ symbol and return the query after it
-- Returns: prefix_pos, query
local function get_at_symbol_query(line, cursor_col)
  local at_pos = nil

  for i = cursor_col, 1, -1 do
    local char = line:sub(i, i)
    if char == "@" then
      at_pos = i
      break
    elseif char == " " then
      -- Allow escaped spaces (preceded by backslash) to be part of the path
      -- If space is NOT preceded by backslash, it breaks the @ context
      if not (i > 1 and line:sub(i - 1, i - 1) == "\\") then
        break
      end
    end
  end

  if not at_pos then
    return nil, nil
  end

  local query = line:sub(at_pos + 1, cursor_col)
  return at_pos, query
end

-- Helper function to find # symbol and return the query after it
-- Allows spaces in the query (conversation titles have spaces)
-- Returns: hash_pos, query
local function get_hash_symbol_query(line, cursor_col)
  local hash_pos = nil

  for i = cursor_col, 1, -1 do
    local char = line:sub(i, i)
    if char == "#" then
      hash_pos = i
      break
    end
  end

  if not hash_pos then
    return nil, nil
  end

  -- Make sure # is at start of line or preceded by a space (not mid-word)
  if hash_pos > 1 and line:sub(hash_pos - 1, hash_pos - 1) ~= " " then
    return nil, nil
  end

  local query = line:sub(hash_pos + 1, cursor_col)
  return hash_pos, query
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
        -- Special bonus for exact dot matches (very important for file extensions)
        if char == "." and str_lower:sub(str_idx, str_idx) == "." then
          score = score + 10
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
  -- Huge bonus for exact matches
  if str_lower == pattern_lower then
    score = score + 100
  end

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

local function normalize_path(path)
  return vim.fs.normalize(path)
end

local function make_relative_path(path, root)
  local normalized_path = normalize_path(path)
  local normalized_root = normalize_path(root)
  local escaped_root = vim.pesc(normalized_root)

  if normalized_path == normalized_root then
    return ""
  end

  local relative = normalized_path:gsub("^" .. escaped_root .. "/?", "")
  return relative == normalized_path and nil or relative
end

local function resolve_query_context(query, project_root)
  local cwd = normalize_path(vim.fn.getcwd())
  local home = normalize_path(vim.loop.os_homedir())
  local safe_query = query or ""
  local dir_part = ""
  local prefix = safe_query

  if safe_query:find("/", 1, true) then
    dir_part, prefix = safe_query:match("^(.*)/([^/]*)$")
    dir_part = dir_part or ""
    prefix = prefix or ""
  end

  local base_dir
  if safe_query:sub(1, 2) == "~/" or safe_query == "~" then
    base_dir = home
  elseif safe_query:sub(1, 1) == "/" then
    base_dir = "/"
  elseif safe_query:match("^%.%./") or safe_query == ".." or safe_query:match("^%./") or safe_query == "." then
    base_dir = cwd
  else
    base_dir = project_root
  end

  local search_dir
  if dir_part == "" then
    search_dir = base_dir
  else
    if safe_query:sub(1, 1) == "/" then
      search_dir = normalize_path(dir_part)
    else
      search_dir = normalize_path(base_dir .. "/" .. dir_part)
    end
  end

  return {
    prefix = prefix,
    dir_part = dir_part,
    search_dir = search_dir,
    base_dir = base_dir,
    home = home,
    cwd = cwd,
    project_root = normalize_path(project_root),
    query = safe_query,
  }
end

local function format_completion_path(full_path, context)
  local normalized_full_path = normalize_path(full_path)
  local relative_to_search = make_relative_path(normalized_full_path, context.search_dir)
  if relative_to_search == nil then
    return nil
  end

  local completion = context.dir_part ~= "" and (context.dir_part .. "/" .. relative_to_search) or relative_to_search

  if context.query:sub(1, 2) == "~/" or context.query == "~" then
    local relative_to_home = make_relative_path(normalized_full_path, context.home)
    if relative_to_home == nil then
      return nil
    end
    completion = "~/" .. relative_to_home
  elseif context.query:sub(1, 1) == "/" then
    completion = normalized_full_path
  elseif
    context.query:match("^%.%./")
    or context.query == ".."
    or context.query:match("^%./")
    or context.query == "."
  then
    local relative_to_cwd = make_relative_path(normalized_full_path, context.cwd)
    if relative_to_cwd then
      completion = relative_to_cwd
    else
      completion = normalized_full_path
    end
  else
    local relative_to_project = make_relative_path(normalized_full_path, context.project_root)
    if relative_to_project then
      completion = relative_to_project
    end
  end

  return completion
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
    local scan_handle = uv.fs_scandir(dir)
    if not scan_handle then
      return
    end

    while #collected < limit do
      local name, type = uv.fs_scandir_next(scan_handle)
      if not name then
        break
      end

      -- Skip hidden files and common ignore patterns
      if
        name:sub(1, 1) ~= "."
        and name ~= "node_modules"
        and name ~= "__pycache__"
        and name ~= "target"
        and name ~= "build"
        and name ~= "dist"
        and name ~= "venv"
        and name ~= ".venv"
        and name ~= "vendor"
      then
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

function files.new(_opts)
  -- Cache for project files
  local file_cache = nil
  local cache_root = nil

  return {
    enabled = function()
      return vim.bo.filetype == "anya-prompt"
    end,

    get_trigger_characters = function()
      return { "@", ".", "/", "#", "\\", "~" }
    end,

    get_completions = function(_self, ctx, callback)
      local line = vim.api.nvim_buf_get_lines(ctx.bufnr, ctx.cursor[1] - 1, ctx.cursor[1], false)[1]
      local cursor_col = ctx.cursor[2]

      local empty = { items = {}, is_incomplete_backward = false, is_incomplete_forward = false }

      -- Check for # prefix first (conversation mentions)
      local hash_pos, hash_query = get_hash_symbol_query(line, cursor_col)
      if hash_pos then
        local items = {}
        local conv_results = vim.fn.AnyaSearchMentions(hash_query or "", 15)
        if conv_results and type(conv_results) == "table" then
          for _, conv in ipairs(conv_results) do
            local conv_id = conv.id or ""
            local title = conv.title or "Untitled conversation"
            table.insert(items, {
              label = title,
              filterText = "#" .. (hash_query or ""),
              kind = 18, -- Reference
              detail = "#" .. conv_id,
              documentation = {
                kind = "markdown",
                value = string.format("**%s**\n\nID: `%s`", title, conv_id),
              },
              textEdit = {
                newText = "#" .. conv_id,
                range = {
                  start = {
                    line = ctx.cursor[1] - 1,
                    character = hash_pos - 1,
                  },
                  ["end"] = {
                    line = ctx.cursor[1] - 1,
                    character = cursor_col,
                  },
                },
              },
            })
          end
        end

        callback({
          items = items,
          is_incomplete_backward = true,
          is_incomplete_forward = true,
        })
        return function() end
      end

      -- Check for @ prefix (file mentions)
      local at_pos, query = get_at_symbol_query(line, cursor_col)

      if not at_pos then
        callback(empty)
        return
      end

      -- For path-related trigger characters, ensure we're still in an @ completion context
      if ctx.trigger_character and vim.tbl_contains({ ".", "/", "~" }, ctx.trigger_character) then
        local has_at_before = false
        for i = cursor_col - 1, 1, -1 do
          local char = line:sub(i, i)
          if char == "@" then
            has_at_before = true
            break
          elseif char == " " then
            break
          end
        end
        if not has_at_before then
          callback(empty)
          return
        end
      end

      local items = {}
      local scored_items = {}

      local project_root = get_project_root()
      local query_context = resolve_query_context(query, project_root)
      local search_dir = query_context.search_dir
      local search_dir_exists = vim.fn.isdirectory(search_dir) == 1
      local candidate_files = {}

      if search_dir_exists and search_dir == project_root then
        -- Refresh cache if project root changed
        if file_cache == nil or cache_root ~= project_root then
          file_cache = {}
          cache_root = project_root
          collect_files(project_root, file_cache, 5000)
        end
        candidate_files = file_cache
      elseif search_dir_exists then
        collect_files(search_dir, candidate_files, 5000)
      end

      for _, file_path in ipairs(candidate_files) do
        local completion_path = file_path
        if
          search_dir ~= project_root
          or query_context.query:match("^~/")
          or query_context.query:match("^%.?%.?/")
          or query_context.query:sub(1, 1) == "/"
        then
          local full_path = normalize_path(search_dir .. "/" .. file_path)
          completion_path = format_completion_path(full_path, query_context)
        end

        if completion_path then
          -- Strip escape characters from query for matching
          local clean_query = (query or ""):gsub("\\ ", " "):gsub("\\", "")
          local matches, score = fuzzy_match(completion_path, clean_query)
          if matches then
            table.insert(scored_items, { path = completion_path, score = score })
          end

        end
      end

      -- Sort by score descending
      table.sort(scored_items, function(a, b)
        return a.score > b.score
      end)

      -- Limit results
      local max_results = 50
      for i = 1, math.min(#scored_items, max_results) do
        local item_data = scored_items[i]
        table.insert(items, {
          label = item_data.path,
          kind = 17, -- File
          insertText = item_data.path:match("%s") and item_data.path:gsub(" ", "\\ ") or item_data.path,
          textEdit = {
            newText = item_data.path:match("%s") and item_data.path:gsub(" ", "\\ ") or item_data.path,
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
        is_incomplete_forward = true, -- Allow continuation after dots
      })

      return function() end
    end,
  }
end

return files
