local M = {}

local function config()
  local ok, anya = pcall(require, "anya")
  local opts = ok and anya.config and anya.config.image_clip or {}
  return opts or {}
end

local function system_text(cmd)
  local result = vim.system(cmd, { text = true }):wait()
  if result.code ~= 0 then
    return ""
  end
  return result.stdout or ""
end

local function clipboard_has_image()
  if vim.fn.executable("wl-paste") == 1 then
    local types = system_text({ "wl-paste", "--list-types" })
    return types:match("image/") ~= nil
  end

  if vim.fn.executable("xclip") == 1 then
    local types = system_text({ "xclip", "-selection", "clipboard", "-t", "TARGETS", "-o" })
    return types:match("image/") ~= nil
  end

  return false
end

local function fallback_paste(mode)
  if mode == "i" then
    vim.api.nvim_feedkeys(vim.api.nvim_replace_termcodes("<C-r>+", true, false, true), "n", false)
  else
    vim.api.nvim_feedkeys('"+p', "n", false)
  end
end

local function get_img_clip()
  local ok, img_clip = pcall(require, "img-clip")
  if ok then
    return img_clip
  end
  return nil
end

function M.setup()
  local img_clip = get_img_clip()
  if not img_clip then
    return false
  end

  local opts = vim.tbl_deep_extend("force", {
    default = {
      dir_path = "images",
      use_absolute_path = false,
      relative_to_current_file = false,
      prompt_for_file_name = false,
      drag_and_drop = {
        enabled = false,
        insert_mode = false,
      },
    },
    filetypes = {
      ["anya-prompt"] = {
        template = "$FILE_PATH",
        url_encode_path = false,
        relative_template_path = false,
        use_cursor_in_template = false,
        insert_mode_after_paste = true,
      },
    },
  }, config())

  img_clip.setup(opts)
  return true
end

function M.paste_image(notify_on_missing)
  local img_clip = get_img_clip()
  if not img_clip then
    if notify_on_missing then
      vim.notify("Anya: img-clip.nvim not installed", vim.log.levels.WARN)
    end
    return false
  end

  local pasted, err = pcall(img_clip.pasteimage)
  if not pasted then
    vim.notify("Anya: failed to paste image: " .. tostring(err), vim.log.levels.WARN)
    return false
  end

  return true
end

function M.paste_from_clipboard(mode)
  if clipboard_has_image() then
    if not M.paste_image(false) then
      fallback_paste(mode)
    end
    return
  end

  fallback_paste(mode)
end

return M
