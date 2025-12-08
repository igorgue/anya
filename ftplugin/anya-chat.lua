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
-- Custom foldtext that handles concealed markers
vim.opt_local.foldtext = [[v:lua.require'anya.foldtext'.get_foldtext()]]

-- Cancel agent response with Ctrl+C
vim.keymap.set("n", "<C-c>", function()
  vim.cmd("Anya cancel")
end, { buffer = true, desc = "Cancel agent response" })

-- Edit approval keymaps are set up by edit_view.setup_keymaps() when edit blocks are rendered
-- This ensures the 1/2 keys only work within edit block extmark ranges

-- Highlight @filepath references using extmarks (works with treesitter)
vim.api.nvim_set_hl(0, "AnyaFileRef", { link = "Constant", default = true })

local fileref_ns = vim.api.nvim_create_namespace("anya_fileref")
local bufnr = vim.api.nvim_get_current_buf()

local function highlight_file_refs()
  vim.api.nvim_buf_clear_namespace(bufnr, fileref_ns, 0, -1)
  local lines = vim.api.nvim_buf_get_lines(bufnr, 0, -1, false)
  for lnum, line in ipairs(lines) do
    for start_col, end_col in line:gmatch("()@[A-Za-z0-9_.~/-]+()") do
      vim.api.nvim_buf_add_highlight(bufnr, fileref_ns, "AnyaFileRef", lnum - 1, start_col - 1, end_col - 1)
    end
  end
end

vim.api.nvim_create_autocmd({ "TextChanged", "TextChangedI", "BufEnter", "BufWinEnter" }, {
  buffer = bufnr,
  callback = highlight_file_refs,
  desc = "Highlight @filepath references",
})

-- Expose globally so streaming can call it
_G.anya_highlight_chat_file_refs = highlight_file_refs

highlight_file_refs()
