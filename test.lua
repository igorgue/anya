-- Test: tool_success marker highlights header line
-- The header line (line before marker) gets AnyaToolSuccess highlight (green, no bg)

-- local text_with_markers = [[**exec | ls ~/**
-- <!-- anya: fold_start, tool_success -->
--  `````
--  Android	 Documents  'Pasted image (2).png'   Public	  bin				 hoppydays	     package.json      todo.md
-- 'Bitwig Studio'	 Downloads  'Pasted image.png'	     Sync	  bun.lock			 mods		     research_acp.md   utils
--  Code		 Music	     Pictures		     Videos	  distributed_system_design.md	 node_modules	     snap
--  Desktop	 Opt	     Projects		     Wallpapers   go				 package-lock.json   tmp
-- `````
-- <!-- anya: fold_end -->]]

local text_with_markers = [[**thinking**
<!-- anya: fold_start, thinking -->
The user asks a question or makes a statement that requires thoughtful consideration or analysis. This marker indicates that the response should involve deeper reflection, reasoning, or exploration of ideas.
<!-- anya: fold_end -->]]

local bufnr = vim.api.nvim_get_current_buf()

require("anya").streaming.output_text(bufnr, text_with_markers, {})

-- vim: wrap :
