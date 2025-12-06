-- Test 1: Inject markers via parameter
local text_plain = [[exec | ls ~/
 `````
 Android	 Documents  'Pasted image (2).png'   Public	  bin				 hoppydays	     package.json      todo.md
'Bitwig Studio'	 Downloads  'Pasted image.png'	     Sync	  bun.lock			 mods		     research_acp.md   utils
 Code		 Music	     Pictures		     Videos	  distributed_system_design.md	 node_modules	     snap
 Desktop	 Opt	     Projects		     Wallpapers   go				 package-lock.json   tmp
`````
]]

-- Test 2: Load text with markers already embedded (e.g., from file/db)
local text_with_markers = [[exec | ls ~/
<!-- anya: fold_start, tool_success -->
 `````
 Android	 Documents  'Pasted image (2).png'   Public	  bin				 hoppydays	     package.json      todo.md
'Bitwig Studio'	 Downloads  'Pasted image.png'	     Sync	  bun.lock			 mods		     research_acp.md   utils
 Code		 Music	     Pictures		     Videos	  distributed_system_design.md	 node_modules	     snap
 Desktop	 Opt	     Projects		     Wallpapers   go				 package-lock.json   tmp
`````
<!-- anya: fold_end -->]]

local bufnr = vim.api.nvim_get_current_buf()

-- Both should produce the same result:
-- Option A: inject markers
-- require("anya").streaming.output_text(bufnr, text_plain, { "fold", "tool_success" })

-- Option B: markers already in text, pass empty list (or nil)
require("anya").streaming.output_text(bufnr, text_with_markers, {})

-- vim: wrap :
