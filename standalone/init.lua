-- standalone/init.lua - A standalone Neovim configuration to use with Anya

-- path
local dir = vim.fs.dirname(vim.uv.fs_realpath(debug.getinfo(1, "S").source:sub(2)))
local parent = vim.fs.dirname(vim.fs.dirname(vim.uv.fs_realpath(debug.getinfo(1, "S").source:sub(2))))

vim.opt.runtimepath:append(dir .. "/danger")

package.path = package.path .. ";" .. parent .. "/lua/?.lua;" .. parent .. "/lua/?/init.lua"
package.path = package.path .. ";" .. dir .. "/which-key/lua/?.lua;" .. dir .. "/which-key/lua/?/init.lua"
package.path = package.path .. ";" .. dir .. "/snacks/lua/?.lua;" .. dir .. "/snacks/lua/?/init.lua"
package.path = package.path .. ";" .. dir .. "/danger/lua/?.lua;" .. dir .. "/danger/lua/?/init.lua"
package.path = package.path .. ";" .. dir .. "/nvim-treesitter/lua/?.lua;" .. dir .. "/nvim-treesitter/lua/?/init.lua"
package.path = package.path .. ";" .. dir .. "/fidget/lua/?.lua;" .. dir .. "/fidget/lua/?/init.lua"
package.path = package.path .. ";" .. dir .. "/blink/lua/?.lua;" .. dir .. "/blink/lua/?/init.lua"
package.path = package.path .. ";" .. dir .. "/noice/lua/?.lua;" .. dir .. "/noice/lua/?/init.lua"
package.path = package.path .. ";" .. dir .. "/nui/lua/?.lua;" .. dir .. "/nui/lua/?/init.lua"
package.path = package.path .. ";" .. dir .. "/nvim-notify/lua/?.lua;" .. dir .. "/nvim-notify/lua/?/init.lua"

vim.opt.runtimepath:append(dir .. "/render-markdown")

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
require("snacks").setup({})
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
require("noice").setup({})
require("blink").setup({
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
      },
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
    providers = {
      snippets = {
        opts = {
          extended_filetypes = {
            jinja = { "html" },
          },
        },
      },
    },
  },
  snippets = {
    preset = "default",
  },

  appearance = {
    -- sets the fallback highlight groups to nvim-cmp's highlight groups
    -- useful for when your theme doesn't support blink.cmp
    -- will be removed in a future release, assuming themes add support
    use_nvim_cmp_as_default = false,
    -- set to 'mono' for 'Nerd Font Mono' or 'normal' for 'Nerd Font'
    -- adjusts spacing to ensure icons are aligned
    nerd_font_variant = "mono",
  },

  completion = {
    accept = {
      -- experimental auto-brackets support
      auto_brackets = {
        enabled = true,
      },
    },
    menu = {
      draw = {
        treesitter = { "lsp" },
      },
    },
    documentation = {
      auto_show = true,
      auto_show_delay_ms = 200,
    },
    ghost_text = {
      enabled = vim.g.ai_cmp,
    },
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
  keymap = {
    preset = "enter",
  },
})
require("anya").setup({})

-- colorscheme
vim.cmd("colorscheme danger")

-- keymaps
vim.keymap.set("n", "<leader>q", "<cmd>q!<CR>", { desc = "Quit" })
vim.keymap.set("i", "<C-q>", "<Esc><cmd>q!<CR>", { desc = "Quit" })

-- autocommands
