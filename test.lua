local text_with_markers = [[<!-- ac: 67f169, 2024-06-27T14:30:00Z -->
# Igor
<!-- am: 604c2d, start, Igor, 2024-06-27T14:30:00Z -->
> Change the streaming code to say "something else" instead of "something".
<!-- am: 604c2d, end, 2024-06-27T14:30:00Z -->
# Agent
<!-- am: f13e20, start, code, gpt-4.1, 2024-06-27T14:30:00Z -->
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

> 15s
<!-- am: f13e20, end, 2024-06-27T14:30:15Z -->
# Igor
<!-- am: 604c2d, start, Igor, 2024-06-27T14:30:00Z -->
> thanks!
<!-- am: 604c2d, end, 2024-06-27T14:30:00Z -->]]

local bufnr = vim.api.nvim_get_current_buf()

require("anya").text.output(bufnr, text_with_markers, {})

-- vim: wrap :
