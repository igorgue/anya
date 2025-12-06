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

-- local text_with_markers = [[27+ 2~ 30- | lua/anya/streaming.lua
-- <!-- anya__markers: fold_start, edit_applied -->
-- ```diff
-- - local old_code = "something"
-- + local new_code = "something else"
-- ```
-- <!-- anya__markers: fold_end -->]]

-- local text_with_markers = [[# Igor | 2:30pm
-- <!-- anya__message: 8f475eb5-15ae-4b46-8da1-e1964b604c2d, Igor, start, 2024-06-27T14:30:00Z -->
-- Change the streaming code to say "something else" instead of "something".
-- <!-- anya__message: 8f475eb5-15ae-4b46-8da1-e1964b604c2d, Igor, end, 2024-06-27T14:30:00Z -->
-- # Agent | Code | gpt-4.1
-- <!-- anya__message: 52f3e5c7-0557-4f96-88e3-d65553f13e20, Agent | Code | gpt-4.1, start, 2024-06-27T14:30:00Z -->
-- 27+ 2~ 30- | lua/anya/streaming.lua
-- <!-- anya__markers: fold_start, edit_applied -->
-- ```diff
-- - local old_code = "something"
-- + local new_code = "something else"
-- ```
-- <!-- anya__markers: fold_end -->
--
-- > 15s
-- <!-- anya__message: 52f3e5c7-0557-4f96-88e3-d65553f13e20, Agent | Code | gpt-4.1, end, 2024-06-27T14:30:15Z -->]]
local text_with_markers = [[<!-- anya__conversation: ee236a3d-c40a-4901-bbea-b04b5467f169, 2024-06-27T14:30:00Z -->
# Igor
<!-- anya__message: 604c2d, start, Igor, 2024-06-27T14:30:00Z -->
> Change the streaming code to say "something else" instead of "something".
<!-- anya__message: 604c2d, end, 2024-06-27T14:30:00Z -->
# Agent
<!-- anya__message: f13e20, start, code, gpt-4.1, 2024-06-27T14:30:00Z -->
27+ 2~ 30- | lua/anya/streaming.lua
<!-- anya__markers: fold_start, edit_applied -->
```diff
- local old_code = "something"
+ local new_code = "something else"
```
<!-- anya__markers: fold_end -->
**exec | ls ~/**
<!-- anya__markers: fold_start, tool_failure -->
 `````
 Android	 Documents  'Pasted image (2).png'   Public	  bin				 hoppydays	     package.json      todo.md
 `````
<!-- anya__markers: fold_end -->

> 15s
<!-- anya__message: f13e20, end, 2024-06-27T14:30:15Z -->
# Igor
<!-- anya__message: 604c2d, start, Igor, 2024-06-27T14:30:00Z -->
> thanks!
<!-- anya__message: 604c2d, end, 2024-06-27T14:30:00Z -->]]

local bufnr = vim.api.nvim_get_current_buf()

require("anya").streaming.output_text(bufnr, text_with_markers, {})

-- Test keymap: press 1 to accept, 2 to reject
-- After running this test, you can test the state update with:
-- :lua require("anya").streaming.update_edit_state_at_cursor("accepted")
-- :lua require("anya").streaming.update_edit_state_at_cursor("rejected")

-- vim: wrap :
