local mentions = {}

-- Highlight group for conversation mentions
vim.api.nvim_set_hl(0, "AnyaConvMention", { link = "Special", default = true })

-- Extract the mention query after @ (alphanumeric, hyphens, underscores only)
local function get_mention_query(line, cursor_col)
  local at_pos = nil

  -- Search backward for @ that could start a conversation mention
  -- Conversation IDs are alphanumeric with hyphens/underscores
  for i = cursor_col, 1, -1 do
    local char = line:sub(i, i)
    if char == "@" then
      at_pos = i
      break
    elseif char == " " then
      -- Space breaks the mention context
      break
    end
  end

  if not at_pos then
    return nil, nil
  end

  -- Get the query after @ up to cursor
  local query = line:sub(at_pos + 1, cursor_col)
  return at_pos, query
end

-- Check if current position is inside a conversation mention context
-- This is distinguished from file mentions by checking if the query
-- looks like a conversation ID (alphanumeric with hyphens, no dots or slashes)
local function is_conversation_mention_context(line, cursor_col)
  local at_pos, query = get_mention_query(line, cursor_col)
  if not at_pos then
    return false, at_pos, query
  end

  -- If query contains / or . it's likely a file path, not a conversation ID
  if query:match("[/.]") then
    return false, at_pos, query
  end

  return true, at_pos, query
end

function mentions.new(_opts)
  return {
    enabled = function()
      return vim.bo.filetype == "anya-prompt"
    end,

    get_trigger_characters = function()
      return { "@" }
    end,

    get_completions = function(_self, ctx, callback)
      local line = vim.api.nvim_buf_get_lines(ctx.bufnr, ctx.cursor[1] - 1, ctx.cursor[1], false)[1]
      local cursor_col = ctx.cursor[2]

      local is_conv, at_pos, query = is_conversation_mention_context(line, cursor_col)

      -- Not in a conversation mention context (might be a file mention)
      if not is_conv or not at_pos then
        callback({
          items = {},
          is_incomplete_backward = false,
          is_incomplete_forward = false,
        })
        return
      end

      -- Search conversations via RPC
      local results = vim.fn.AnyaSearchMentions(query or "", 20)

      if not results or type(results) ~= "table" then
        callback({
          items = {},
          is_incomplete_backward = false,
          is_incomplete_forward = false,
        })
        return
      end

      local items = {}
      for _, conv in ipairs(results) do
        local conv_id = conv.id or ""
        local title = conv.title or "Untitled"

        -- Show ID in the completion menu with title as description
        table.insert(items, {
          label = conv_id,
          kind = 18, -- Reference
          detail = title,
          documentation = {
            kind = "markdown",
            value = string.format("**%s**\n\nID: `%s`", title, conv_id),
          },
          insertText = conv_id,
          textEdit = {
            newText = conv_id,
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

return mentions
