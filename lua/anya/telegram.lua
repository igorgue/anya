local M = {}

local function notify(message, level)
  vim.notify(message, level or vim.log.levels.INFO, { title = "Anya Telegram" })
end

local function shell_escape_single(str)
  return "'" .. tostring(str):gsub("'", "'\\''") .. "'"
end

local function qr_lines(text)
  if not text or text == "" then
    return {}
  end

  if vim.fn.executable("qrencode") == 1 then
    local cmd = "qrencode -t UTF8 -m 2 " .. shell_escape_single(text)
    local out = vim.fn.systemlist(cmd)
    if vim.v.shell_error == 0 and out and #out > 0 then
      return out
    end
  end

  if vim.fn.executable("python3") == 1 then
    local py = [[
import sys
try:
    import qrcode
except Exception:
    sys.exit(2)
qr = qrcode.QRCode(border=0)
qr.add_data(sys.argv[1])
qr.make(fit=True)
qr.print_ascii(tty=False, invert=False)
]]
    local cmd = "python3 -c " .. shell_escape_single(py) .. " " .. shell_escape_single(text)
    local out = vim.fn.systemlist(cmd)
    if vim.v.shell_error == 0 and out and #out > 0 then
      return out
    end
  end

  return {}
end

local function build_lines(message, code, url)
  local lines = vim.split(message or "", "\n", { plain = true })
  if url and url ~= "" then
    table.insert(lines, "")
    table.insert(lines, "## QR code")
    table.insert(lines, "")
    local qr = qr_lines(url)
    if #qr > 0 then
      for _, line in ipairs(qr) do
        table.insert(lines, line)
      end
    else
      table.insert(lines, "QR renderer not available.")
      table.insert(lines, "Install `qrencode` or Python's `qrcode` package to render the QR here.")
    end
    table.insert(lines, "")
    table.insert(lines, "Telegram deep link:")
    table.insert(lines, url)
  end
  return lines
end

local function open_with_snacks(message, code, url)
  local snacks_ok, Snacks = pcall(require, "snacks")
  if not snacks_ok or not Snacks.scratch or not Snacks.scratch.open then
    return false
  end

  local lines = build_lines(message, code, url)
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
        copy_url = {
          "yu",
          function()
            vim.fn.setreg("+", url or "")
            notify("Copied Telegram pairing URL")
          end,
          desc = "copy Telegram pairing URL",
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

function M.show_pairing(message, code, url)
  message = message or vim.g.anya_telegram_pairing_message
  code = code or vim.g.anya_telegram_pairing_code
  url = url or vim.g.anya_telegram_pairing_url

  if not message or message == "" then
    notify("No Telegram pairing information available yet. Run :Anya telegram pair first.", vim.log.levels.WARN)
    return
  end

  vim.g.anya_telegram_pairing_message = message
  vim.g.anya_telegram_pairing_code = code or ""
  vim.g.anya_telegram_pairing_url = url or ""

  if not open_with_snacks(message, code or "", url or "") then
    notify(message)
  end
end

function M.reopen_pairing()
  local code = vim.g.anya_telegram_pairing_code
  if code and code ~= "" then
    M.show_pairing(vim.g.anya_telegram_pairing_message, code, vim.g.anya_telegram_pairing_url)
  else
    vim.cmd("Anya telegram pair")
  end
end

return M
