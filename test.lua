local text_with_markers = [[<!-- am: 604c2d -->
> Change the streaming code to say "something else" instead of "something".
<!-- am: f13e20 -->
27+ 2~ 30- | lua/anya/streaming.lua
<!-- at: fold_start, edit_applied -->
```diff
- local old_code = "something"
+ local new_code = "something else"
```
<!-- at: fold_end -->
**exec | ls ~/**
<!-- at: fold_start, tool_failure -->
 `````
 Android	 Documents  'Pasted image (2).png'   Public	  bin				 hoppydays	     package.json      todo.md
 `````
<!-- at: fold_end -->
<!-- am: 604c2d -->
> thanks!]]

local bufnr = vim.api.nvim_get_current_buf()

require("anya").text.output(bufnr, text_with_markers, {})

-- vim: wrap :
