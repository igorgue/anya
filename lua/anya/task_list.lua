local M = {
  latest = nil,
}

local function normalize_items(items)
  local normalized = {}
  for _, item in ipairs(items or {}) do
    local text = tostring(item.text or ""):gsub("^%s+", ""):gsub("%s+$", "")
    if text ~= "" then
      table.insert(normalized, {
        text = text,
        status = tostring(item.status or "pending"),
      })
    end
  end
  return normalized
end

function M.format(title, items)
  local lines = {}
  local status_map = {
    done = "[x]",
    in_progress = "[-]",
    pending = "[ ]",
  }

  for _, item in ipairs(normalize_items(items)) do
    table.insert(lines, string.format("%s %s", status_map[item.status] or "[ ]", item.text))
  end

  if #lines == 0 then
    lines = { "(no tasks)" }
  end

  return title ~= "" and title or "Tasks", table.concat(lines, "\n")
end

function M.set_latest(title, items)
  M.latest = {
    title = tostring(title or ""),
    items = normalize_items(items),
  }
end

function M.notify(title, items)
  local notify_title, body = M.format(tostring(title or ""), items or {})
  vim.notify(body, vim.log.levels.INFO, { title = notify_title })
end

function M.update_and_notify(title, items)
  M.set_latest(title, items)
  M.notify(title, items)
end

function M.show_latest()
  if not M.latest then
    vim.notify("No task list has been published yet.", vim.log.levels.INFO, { title = "Anya Tasks" })
    return
  end

  M.notify(M.latest.title, M.latest.items)
end

return M
