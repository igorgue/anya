-- standalone/init.lua - A standalone Neovim configuration to use with Anya

-- path
local dir = vim.fs.dirname(vim.uv.fs_realpath(debug.getinfo(1, "S").source:sub(2)))
local parent = vim.fs.dirname(vim.fs.dirname(vim.uv.fs_realpath(debug.getinfo(1, "S").source:sub(2))))

vim.opt.runtimepath:prepend(parent)
vim.opt.runtimepath:append(dir .. "/danger")

package.path = package.path .. ";" .. parent .. "/lua/?.lua;" .. parent .. "/lua/?/init.lua"
package.path = package.path .. ";" .. dir .. "/which-key/lua/?.lua;" .. dir .. "/which-key/lua/?/init.lua"
package.path = package.path .. ";" .. dir .. "/snacks/lua/?.lua;" .. dir .. "/snacks/lua/?/init.lua"
package.path = package.path .. ";" .. dir .. "/danger/lua/?.lua;" .. dir .. "/danger/lua/?/init.lua"
package.path = package.path .. ";" .. dir .. "/nvim-treesitter/lua/?.lua;" .. dir .. "/nvim-treesitter/lua/?/init.lua"
package.path = package.path .. ";" .. dir .. "/fidget/lua/?.lua;" .. dir .. "/fidget/lua/?/init.lua"
package.path = package.path .. ";" .. dir .. "/blink-cmp/lua/?.lua;" .. dir .. "/blink-cmp/lua/?/init.lua"
package.path = package.path .. ";" .. dir .. "/noice/lua/?.lua;" .. dir .. "/noice/lua/?/init.lua"
package.path = package.path .. ";" .. dir .. "/nui/lua/?.lua;" .. dir .. "/nui/lua/?/init.lua"
package.path = package.path .. ";" .. dir .. "/nvim-notify/lua/?.lua;" .. dir .. "/nvim-notify/lua/?/init.lua"
package.path = package.path .. ";" .. dir .. "/mini-surround/lua/?.lua;" .. dir .. "/mini-surround/lua/?/init.lua"
package.path = package.path .. ";" .. dir .. "/marks-nvim/lua/?.lua;" .. dir .. "/marks-nvim/lua/?/init.lua"

vim.opt.runtimepath:append(dir .. "/which-key")
vim.opt.runtimepath:append(dir .. "/render-markdown")
vim.opt.runtimepath:append(dir .. "/blink-cmp")
vim.opt.runtimepath:append(dir .. "/vim-indent-object")
vim.opt.runtimepath:append(dir .. "/mini-surround")
vim.opt.runtimepath:append(dir .. "/marks-nvim")
vim.opt.runtimepath:append(dir .. "/vim-matchup")

-- opts
vim.opt.signcolumn = "auto"
vim.opt.statuscolumn = ""
vim.opt.laststatus = 0
vim.opt.number = false
vim.opt.relativenumber = false
vim.opt.cursorline = false
vim.opt.list = false
vim.opt.spell = false
vim.opt.wrap = false
vim.opt.linebreak = true
vim.opt.showbreak = ""
vim.opt.timeout = true
vim.opt.timeoutlen = 500
vim.opt.ignorecase = true
vim.opt.smartcase = true
vim.opt.wildignorecase = true
vim.opt.pumblend = 0
vim.opt.backspace = { "indent", "eol", "start" }
vim.opt.scrolloff = 3
vim.opt.foldmethod = "manual"
vim.opt.diffopt = {
  algorithm = "histogram",
  linematch = 60,
  "internal",
  "indent-heuristic",
  "filler",
  "closeoff",
  "iwhite",
  "vertical",
}
vim.opt.listchars = {
  tab = "──",
  lead = "·",
  trail = "·",
  nbsp = "␣",
  eol = "↵",
  precedes = "«",
  extends = "»",
}
vim.opt.fillchars = {
  -- "vert:▏",
  vert = "│",
  diff = "╱",
  foldclose = "",
  foldopen = "",
  fold = " ",
  msgsep = "─",
  eob = " ",
}
vim.opt.writebackup = true
vim.opt.undofile = true
vim.opt.isfname:append(":")
vim.opt.smoothscroll = false

vim.opt.clipboard = "unnamedplus"

-- Set window title to "Anya"
vim.opt.title = true
vim.opt.titlestring = "Anya"

vim.opt.shortmess:append("I")

if vim.o.diff ~= false then
  vim.opt.list = false
  vim.opt.wrap = false

  vim.opt.signcolumn = "no"
  vim.opt.cursorline = true
  vim.opt.number = true
end

-- sets the tabline to not show x, a very simple tabline
local function nox_tab_label(n)
  local buflist = vim.fn.tabpagebuflist(n)
  local winnr = vim.fn.tabpagewinnr(n)
  local name = vim.fn.fnamemodify(vim.fn.bufname(buflist[winnr]), ":t")
  if name == "" then
    return "[No Name]"
  end
  return name
end

function _G.NoXTabLine()
  local s = ""
  local total = vim.fn.tabpagenr("$")
  local current = vim.fn.tabpagenr()
  for i = 1, total do
    s = s .. (i == current and "%#TabLineSel#" or "%#TabLine#") .. "%" .. i .. "T " .. nox_tab_label(i) .. " "
  end
  s = s .. "%#TabLineFill#%T"
  if total > 1 then
    s = s .. "%=%#TabLine#%999X"
  end
  return s
end

vim.o.tabline = "%!v:lua.NoXTabLine()"

vim.g.mapleader = " "
vim.g.maplocalleader = "\\"

-- plugin setup
require("which-key").setup({})
require("snacks").setup({ input = { enabled = true }, picker = { enabled = true, ui_select = true } })
require("danger").setup({
  style = "dark",
  alacritty = false,
  kitty = false,
})
require("nvim-treesitter").setup({
  install_dir = vim.fn.stdpath("data") .. "/site",
})
require("nvim-treesitter").install({
  "rust",
  "javascript",
  "zig",
  "elixir",
  "c",
  "cpp",
  "go",
  "python",
  "ruby",
  "javascript",
  "typescript",
  "html",
  "css",
  "lua",
  "markdown",
  "markdown_inline",
  "tsx",
  "json",
  "yaml",
  "toml",
  "bash",
  "fish",
  "zsh",
})
vim.api.nvim_create_autocmd("FileType", {
  callback = function(args)
    pcall(vim.treesitter.start, args.buf)
  end,
})
require("fidget").setup({})
require("render-markdown").setup({
  file_types = { "anya-chat", "markdown" },
  preset = "lazy",
  code = {
    disable_background = true,
  },
  restart_highlighter = false,
  completions = {
    blink = { enabled = false },
    lsp = { enabled = false },
  },
  heading = {
    ---@diagnostic disable-next-line: assign-type-mismatch
    icons = false,
  },
})
require("notify").setup({})
require("noice").setup({
  presets = {
    bottom_search = false,
    command_palette = false,
    long_message_to_split = false,
  },
})
require("blink.cmp").setup({
  fuzzy = { implementation = "lua" },
  completion = {
    list = {
      selection = {
        preselect = true,
        auto_insert = true,
      },
    },
    accept = {
      auto_brackets = {
        enabled = true,
      },
    },
    menu = {
      auto_show = true,
      draw = {
        columns = {
          { "kind_icon", "label", gap = 1 },
          { "label_description", "source_id" },
        },
        treesitter = { "lsp" },
      },
    },
    documentation = {
      auto_show = true,
      auto_show_delay_ms = 200,
    },
    ghost_text = {
      enabled = false,
    },
  },
  signature = {
    enabled = false,
    trigger = {
      show_on_insert_on_trigger_character = false,
    },
  },
  keymap = {
    preset = "enter",
    ["<C-p>"] = { "select_prev", "fallback" },
    ["<C-n>"] = { "select_next", "fallback" },
    ["<S-Tab>"] = { "fallback" },
  },
  sources = {
    default = { "anya_files", "anya_commands" },
    providers = {
      snippets = {
        opts = {
          extended_filetypes = {
            jinja = { "html" },
          },
        },
      },
      anya_files = {
        name = "Anya Files",
        module = "anya.blink.files",
        enabled = function()
          return vim.bo.filetype == "anya-prompt"
        end,
      },
      anya_commands = {
        name = "Anya Commands",
        module = "anya.blink.commands",
        enabled = function()
          return vim.bo.filetype == "anya-prompt"
        end,
      },
    },
  },
  snippets = {
    preset = "default",
  },
  appearance = {
    use_nvim_cmp_as_default = false,
    nerd_font_variant = "mono",
  },
  cmdline = {
    enabled = true,
    keymap = {
      preset = "cmdline",
      ["<Right>"] = false,
      ["<Left>"] = false,
    },
    completion = {
      list = { selection = { preselect = false } },
      menu = {
        auto_show = function(_)
          return vim.fn.getcmdtype() == ":"
        end,
      },
      ghost_text = { enabled = true },
    },
  },
})
-- vim-indent-object (no setup needed, just load via runtimepath)

-- mini-surround
require("mini.surround").setup({})

-- marks.nvim
require("marks").setup({})

-- vim-matchup
vim.g.matchup_surround_enabled = 1
vim.g.matchup_transmute_enabled = 1

-- keymaps for new plugins
-- mini-surround visual mode
vim.keymap.set("x", "S", [[:<C-u>lua MiniSurround.add('visual')<CR>]], { desc = "Add Surrounding" })

-- marks.nvim keymaps
vim.keymap.set("n", "<leader>M", function()
  local input = vim.fn.input("Mark to delete:")
  if input:gsub("^%s*(.-)%s*$", "%1") == "" then
    return
  end
  vim.cmd("delmarks " .. input)
end, { desc = "Delete mark" })
vim.keymap.set("n", "<leader>mm", function()
  Snacks.picker.marks()
end, { desc = "Search marks" })
vim.keymap.set("n", "<leader>md", "<cmd>delmarks!<cr>", { desc = "Delete local marks" })
vim.keymap.set("n", "<leader>mD", "<cmd>delmarks!<cr><cmd>delmarks A-Z<cr>", { desc = "Delete all marks" })

-- indent-object keymaps
vim.keymap.set("n", "<c-space>", "<cmd>normal viI<cr>", { desc = "Inner Indent Level" })
vim.keymap.set("x", "<c-space>", "<cmd>normal iI<cr>", { desc = "Inner Indent Level" })

require("anya").setup({})

-- colorscheme
vim.cmd("colorscheme danger")

-- keymaps
vim.keymap.set("n", "<leader>q", "<cmd>qa!<CR>", { desc = "Quit" })
vim.keymap.set("i", "<C-q>", "<Esc><cmd>qa!<CR>", { desc = "Quit" })
