local markers = require("anya.markers")
local text = require("anya.text")
local conversation = require("anya.conversation")
local picker = require("anya.picker")
local history = require("anya.history")
local task_list = require("anya.task_list")

local config = {
  start_in_insert = false,
  image_clip = {},
}

local M = {
  config = config,
  markers = markers,
  text = text,
  conversation = conversation,
  picker = picker,
  history = history,
  task_list = task_list,
}

function M.setup(opts)
  opts = opts or {}
  for k, v in pairs(opts) do
    config[k] = v
  end
end

return M
