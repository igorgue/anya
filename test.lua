-- Test: tool_success marker highlights header line
-- The header line (line before marker) gets AnyaToolSuccess highlight (green, no bg)

-- local text_with_markers = [[**exec | ls ~/**
-- <!-- anya__markers: fold_start, tool_success -->
--  `````
--  Android	 Documents  'Pasted image (2).png'   Public	  bin				 hoppydays	     package.json      todo.md
-- 'Bitwig Studio'	 Downloads  'Pasted image.png'	     Sync	  bun.lock			 mods		     research_acp.md   utils
--  Code		 Music	     Pictures		     Videos	  distributed_system_design.md	 node_modules	     snap
--  Desktop	 Opt	     Projects		     Wallpapers   go				 package-lock.json   tmp
-- `````
-- <!-- anya__markers: fold_end -->]]

-- local text_with_markers = [[**thinking**
-- <!-- anya__markers: fold_start, thinking -->
-- The user asks a question or makes a statement that requires thoughtful consideration or analysis. This marker indicates that the response should involve deeper reflection, reasoning, or exploration of ideas.
-- <!-- anya__markers: fold_end -->]]

-- Test: edit tool with diff info and accept/reject widget
-- Header format: "27+ 2~ 30- | filename.ext"
-- The diff info is parsed from the header line, not encoded in the marker

local text_with_markers = [[27+ 2~ 30- | lua/anya/streaming.lua
<!-- anya__markers: fold_start, edit_applied -->
```diff
- local old_code = "something"
+ local new_code = "something else"
```
<!-- anya__markers: fold_end -->]]

local bufnr = vim.api.nvim_get_current_buf()

require("anya").streaming.output_text(bufnr, text_with_markers, {})

-- Test keymap: press 1 to accept, 2 to reject
-- After running this test, you can test the state update with:
-- :lua require("anya").streaming.update_edit_state_at_cursor("accepted")
-- :lua require("anya").streaming.update_edit_state_at_cursor("rejected")

-- vim: wrap :
