local commands = {}

-- Available slash commands
local AVAILABLE_COMMANDS = {
  {
    label = "/clear",
    description = "Clear chat history",
    kind = vim.lsp and vim.lsp.CompletionItemKind and vim.lsp.CompletionItemKind.Keyword or 14,
  },
  {
    label = "/cancel",
    description = "Cancel current request",
    kind = vim.lsp and vim.lsp.CompletionItemKind and vim.lsp.CompletionItemKind.Keyword or 14,
  },
  {
    label = "/file",
    description = "Open file picker and add files to prompt",
    kind = vim.lsp and vim.lsp.CompletionItemKind and vim.lsp.CompletionItemKind.Keyword or 14,
  },
  {
    label = "/compact",
    description = "Compact conversation context",
    kind = vim.lsp and vim.lsp.CompletionItemKind and vim.lsp.CompletionItemKind.Keyword or 14,
  },
  {
    label = "/help",
    description = "Show help message",
    kind = vim.lsp and vim.lsp.CompletionItemKind and vim.lsp.CompletionItemKind.Keyword or 14,
  },
}

function commands.new(opts)
  return {
    -- Check if this source should be enabled for the current buffer
    enabled = function()
      local ft = vim.bo.filetype
      return ft == "anya-prompt"
    end,

    get_trigger_characters = function()
      return { "/" }
    end,

    get_completions = function(self, ctx, callback)
      -- Get full line content using vim API since ctx.line only contains the keyword bounds
      local line = vim.api.nvim_buf_get_lines(ctx.bufnr, ctx.cursor[1] - 1, ctx.cursor[1], false)[1]

      -- Get cursor position from context
      local cursor_col = ctx.cursor[2] -- cursor is {line, col} in 1-indexed format

      -- Find the / symbol and get the base text after it
      local slash_pos = nil
      local base = ""

      for i = cursor_col, 1, -1 do -- Include cursor position
        local char = line:sub(i, i)
        if char == "/" then
          slash_pos = i
          break
        elseif char == " " then
          break
        end
      end

      -- Only provide completions if we found a / symbol at the beginning of line or after space
      if not slash_pos then
        callback({
          items = {},
          is_incomplete_backward = false,
          is_incomplete_forward = false,
        })
        return
      end

      -- Check if this / is at the beginning of line or after a space
      local prev_char = slash_pos > 1 and line:sub(slash_pos - 1, slash_pos - 1) or " "
      if prev_char ~= " " and slash_pos ~= 1 then
        callback({
          items = {},
          is_incomplete_backward = false,
          is_incomplete_forward = false,
        })
        return
      end

      -- Extract base text after /
      base = line:sub(slash_pos + 1, cursor_col - 1):lower()

      local items = {}

      for _, cmd in ipairs(AVAILABLE_COMMANDS) do
        local cmd_label = cmd.label:sub(2):lower() -- Remove / and lowercase for comparison
        if cmd_label:find(base, 1, true) == 1 or base == "" then
          table.insert(items, {
            label = cmd.label,
            kind = cmd.kind,
            documentation = {
              kind = "markdown",
              value = cmd.description,
            },
            insertText = cmd.label,
            insertTextFormat = vim.lsp.protocol.InsertTextFormat.PlainText,
            -- Replace from / to cursor position
            textEdit = {
              newText = cmd.label,
              range = {
                start = {
                  line = ctx.cursor[1] - 1, -- cursor is 1-indexed, LSP needs 0-indexed
                  character = slash_pos - 1, -- Convert to 0-indexed (position before /)
                },
                ["end"] = {
                  line = ctx.cursor[1] - 1, -- cursor is 1-indexed, LSP needs 0-indexed
                  character = cursor_col, -- Convert to 0-indexed (cursor position, include cursor char)
                },
              },
            },
          })
        end
      end

      -- Use proper blink.cmp callback format
      callback({
        items = items,
        is_incomplete_backward = false,
        is_incomplete_forward = false,
      })

      -- Return cancellation function
      return function()
        -- Cancel any pending async operations if needed
      end
    end,
  }
end

return commands
