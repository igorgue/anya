local M = {}

local function notify(message, level)
  vim.notify(message, level or vim.log.levels.INFO, { title = "Anya Telegram" })
end

local function open_with_snacks(message, code)
  local snacks_ok, Snacks = pcall(require, "snacks")
  if not snacks_ok or not Snacks.scratch or not Snacks.scratch.open then
    return false
  end

  local lines = vim.split(message, "\n", { plain = true })
  local win = Snacks.scratch.open({
    name = "Anya Telegram Pairing",
    ft = "markdown",
    icon = "󰒌",
    autowrite = false,
    win = {
      style = "scratch",
      wo = { winhighlight = "NormalFloat:Normal" },
      bo = {
        buftype = "nofile",
        bufhidden = "wipe",
        swapfile = false,
        filetype = "markdown",
      },
      keys = {
        copy_code = {
          "yc",
          function()
            vim.fn.setreg("+", code or "")
            notify("Copied Telegram pairing code")
          end,
          desc = "copy pairing code",
          mode = { "n" },
        },
        copy_command = {
          "yC",
          function()
            vim.fn.setreg("+", "/connect " .. (code or ""))
            notify("Copied Telegram /connect command")
          end,
          desc = "copy /connect command",
          mode = { "n" },
        },
      },
    },
  })

  if win and win.buf then
    vim.bo[win.buf].modifiable = true
    vim.api.nvim_buf_set_lines(win.buf, 0, -1, false, lines)
    vim.bo[win.buf].modifiable = false
    vim.bo[win.buf].readonly = true
  end

  return true
end

function M.show_pairing(message, code)
  message = message or vim.g.anya_telegram_pairing_message
  code = code or vim.g.anya_telegram_pairing_code

  if not message or message == "" then
    notify("No Telegram pairing information available yet. Run :Anya telegram pair first.", vim.log.levels.WARN)
    return
  end

  vim.g.anya_telegram_pairing_message = message
  vim.g.anya_telegram_pairing_code = code or ""

  if not open_with_snacks(message, code or "") then
    notify(message)
  end
end

function M.reopen_pairing()
  M.show_pairing(vim.g.anya_telegram_pairing_message, vim.g.anya_telegram_pairing_code)
end

return M
