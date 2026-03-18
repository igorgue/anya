local config = {
  start_in_insert = false,
  image_clip = {},
}

local M = {
  config = config,
}

-- Lazy-load submodules on first access
setmetatable(M, {
  __index = function(t, key)
    local ok, mod = pcall(require, "anya." .. key)
    if ok then
      rawset(t, key, mod)
      return mod
    end
    return nil
  end,
})

function M.setup(opts)
  opts = opts or {}
  for k, v in pairs(opts) do
    config[k] = v
  end
end

return M
