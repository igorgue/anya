local M = {}

-- Store pending completion callbacks
local pending_callbacks = {}

-- Generate unique callback ID
local function generate_callback_id()
  return tostring(os.clock()) .. tostring(math.random(10000, 99999))
end

-- Callback function called from Python
function M.file_completion_callback(matches, callback_id)
  local callback = pending_callbacks[callback_id]
  if callback then
    callback(matches)
    pending_callbacks[callback_id] = nil
  end
end

-- Wrapper for async file completion
function M.get_file_completions_async(base, callback)
  local callback_id = generate_callback_id()
  pending_callbacks[callback_id] = callback

  -- Call the Python async function
  vim.fn.AgentCompleteAsync(base, callback_id)
end

-- Synchronous file completion using Lua's built-in functions
function M.get_file_completions_sync(base, limit)
  limit = limit or 50
  local matches = {}

  -- Handle empty base - show files in current directory
  if base == "" then
    base = ""
  end

  -- Determine the directory to search and the prefix to match
  local dir_part
  local prefix

  if base:match("/") then
    -- Path contains directory separator
    dir_part = base:match("^(.*/)[^/]*$")
    prefix = base:match("^.*/([^/]*)$") or ""
  else
    -- Just filename in current directory
    dir_part = ""
    prefix = base
  end

  -- Convert to absolute path for scanning
  local search_dir
  if dir_part == "" then
    search_dir = vim.fn.getcwd()
  elseif dir_part:match("^/") then
    search_dir = dir_part
  else
    search_dir = vim.fn.getcwd() .. "/" .. dir_part
  end

  -- Remove trailing slash for directory scanning
  if search_dir:sub(-1) == "/" then
    search_dir = search_dir:sub(1, -2)
  end

  local handle = vim.loop.fs_scandir(search_dir)
  if not handle then
    return matches
  end

  local count = 0
  while count < limit do
    local name, type = vim.loop.fs_scandir_next(handle)
    if not name then
      break
    end

    -- Skip hidden files and .git
    if name:sub(1, 1) ~= "." and name ~= ".git" then
      -- Check if the name matches our prefix
      if prefix == "" or name:lower():find(prefix:lower(), 1, true) == 1 then
        local display_name = name

        -- For directories, add trailing slash
        if type == "directory" then
          display_name = name .. "/"
        end

        -- Reconstruct the completion path
        local completion = dir_part .. display_name

        table.insert(matches, completion)
        count = count + 1
      end
    end
  end

  -- Sort results: directories first, then files, alphabetically
  table.sort(matches, function(a, b)
    local a_is_dir = a:sub(-1) == "/"
    local b_is_dir = b:sub(-1) == "/"
    if a_is_dir and not b_is_dir then
      return true
    elseif not a_is_dir and b_is_dir then
      return false
    else
      return a:lower() < b:lower()
    end
  end)

  return matches
end

-- Set up the global callback function that Python can call
function M.setup()
  -- Make the callback function globally accessible to Python
  _G.agent_nvim_blink_file_completion_callback = M.file_completion_callback
end

-- Set up automatically when module is loaded
M.setup()

return M
