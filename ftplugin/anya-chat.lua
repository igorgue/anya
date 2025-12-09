-- Filetype plugin for anya-chat buffer
-- Configures the chat display buffer

-- Register markdown treesitter for syntax highlighting
vim.treesitter.language.register("markdown", "anya-chat")

-- Buffer-local options
vim.opt_local.wrap = true
vim.opt_local.linebreak = true
vim.opt_local.breakindent = true
vim.opt_local.showbreak = ""
vim.opt_local.number = false
vim.opt_local.relativenumber = false
vim.opt_local.signcolumn = "no"
vim.opt_local.foldmethod = "manual"
vim.opt_local.foldenable = true
vim.opt_local.modifiable = true
vim.opt_local.spell = false
vim.opt_local.winbar = ""
-- Custom foldtext that handles concealed markers
vim.opt_local.foldtext = [[v:lua.require'anya.foldtext'.get_foldtext()]]

-- Cancel agent response with Ctrl+C
vim.keymap.set("n", "<C-c>", function()
  vim.cmd("Anya cancel")
end, { buffer = true, desc = "Cancel agent response" })

-- Section navigation: jump between # headers (# User, # Anya, etc.)
local function jump_to_header(direction)
  local pattern = "^# "
  local flags = direction == "next" and "W" or "bW"
  local found = vim.fn.search(pattern, flags)
  if found == 0 then
    -- Wrap around if not found
    local wrap_flags = direction == "next" and "w" or "bw"
    vim.fn.search(pattern, wrap_flags)
  end
end

vim.keymap.set("n", "]]", function()
  jump_to_header("next")
end, { buffer = true, desc = "Jump to next header" })

vim.keymap.set("n", "[[", function()
  jump_to_header("prev")
end, { buffer = true, desc = "Jump to previous header" })

-- Edit approval keymaps are set up by edit_view.setup_keymaps() when edit blocks are rendered
-- This ensures the 1/2 keys only work within edit block extmark ranges

-- Highlight @filepath references and /commands using extmarks (works with treesitter)
vim.api.nvim_set_hl(0, "AnyaFileRef", { link = "Constant", default = true })
vim.api.nvim_set_hl(0, "AnyaSlashCommand", { link = "Special", default = true })

local highlight_ns = vim.api.nvim_create_namespace("anya_highlights")
local bufnr = vim.api.nvim_get_current_buf()

local function highlight_refs()
  vim.api.nvim_buf_clear_namespace(bufnr, highlight_ns, 0, -1)
  local lines = vim.api.nvim_buf_get_lines(bufnr, 0, -1, false)
  for lnum, line in ipairs(lines) do
    -- Track file ref ranges to avoid highlighting commands inside them
    local file_ranges = {}

    -- Highlight @filepath references (e.g., @src/main.lua, @/home/user/file.txt)
    for start_col, end_col in line:gmatch("()@[A-Za-z0-9_.~/-]+()") do
      vim.api.nvim_buf_add_highlight(bufnr, highlight_ns, "AnyaFileRef", lnum - 1, start_col - 1, end_col - 1)
      table.insert(file_ranges, { start_col - 1, end_col - 1 })
    end

    -- Highlight /commands (single word, at start or after space, not inside file refs)
    -- Pattern: start of line or space, then /letters, then end or space
    for start_col, cmd, end_col in line:gmatch("()(/[A-Za-z]+)()") do
      local sc = start_col - 1
      -- Check if at start of line or preceded by space
      local preceded_ok = sc == 0 or line:sub(sc, sc) == " "
      -- Check if followed by end of line or space
      local ec = end_col - 1
      local followed_ok = ec >= #line or line:sub(end_col, end_col) == " "
      -- Check not inside a file ref
      local inside_fileref = false
      for _, range in ipairs(file_ranges) do
        if sc >= range[1] and sc < range[2] then
          inside_fileref = true
          break
        end
      end
      if preceded_ok and followed_ok and not inside_fileref then
        vim.api.nvim_buf_add_highlight(bufnr, highlight_ns, "AnyaSlashCommand", lnum - 1, sc, ec)
      end
    end
  end
end

vim.api.nvim_create_autocmd({ "TextChanged", "TextChangedI", "BufEnter", "BufWinEnter" }, {
  buffer = bufnr,
  callback = highlight_refs,
  desc = "Highlight @filepath and /command references",
})

-- Expose globally so streaming can call it
_G.anya_highlight_chat_file_refs = highlight_refs

highlight_refs()
