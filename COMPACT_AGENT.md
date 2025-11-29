# CompactAgent Implementation Plan

## 1. Purpose and Scope

### Purpose
The `CompactAgent` is a specialized agent designed to analyze and condense conversation context while preserving essential information. Its primary goals are:
- Reduce token usage by summarizing lengthy conversation history
- Maintain key information, context, and ongoing tasks
- Enable efficient continuation of conversations without losing critical details
- Provide user control over the summarization process through preview and approval

### Scope
- **In-scope**: 
  - Context analysis and summarization
  - Integration with existing slash command system
  - Preview modal with Snacks.win
  - User approval workflow
  - Seamless context replacement in AgentContent buffer
  
- **Out-of-scope**:
  - Modifying the underlying agent architecture
  - Changing how other tools work
  - Altering the core conversation flow beyond compaction

## 2. Architecture and Components

### Agent Architecture Overview

The CompactAgent feature introduces a **NEW AGENT** created with the OpenAI Agents SDK, separate from the main conversation agent. This specialized approach has several advantages:

#### Why a Separate Agent?
- **Specialized Expertise**: The compact agent has a system prompt specifically tuned for analysis and summarization
- **Independent Tools**: It can have tools optimized for context analysis without cluttering the main agent's toolset
- **Model Flexibility**: Can use different models (via `AGENT_COMPACT_MODEL`) optimized for summarization tasks
- **Clean Separation**: Main conversation flow remains unchanged while adding compaction capability
- **Focused Processing**: The agent can concentrate solely on identifying and preserving critical information

#### Agent Relationship
```
Main Agent (conversation)
    ↓ User triggers /compact
CompactAgent (specialized)
    ↓ Analyzes and summarizes
Preview Interface (user approval)
    ↓ If approved
Main Agent (continues with compacted context)
```

### Core Components

#### 2.1 CompactAgent Class
```python
class CompactAgent:
    """
    Specialized agent for context compaction and summarization.
    
    This is a NEW AGENT created with OpenAI Agents SDK, specifically
    designed for summarization tasks with its own system prompt and
    tools focused on context analysis and compaction.
    """
    def __init__(self, model, logger):
        self.model = model
        self.logger = logger
        self.agent = self._create_compact_agent()
        
    def _create_compact_agent(self) -> Agent:
        """Create specialized summarization agent with custom system prompt."""
        # Custom system prompt oriented toward analysis and summarization
        system_prompt = """
        You are a Context Compaction Agent, specialized in analyzing and 
        summarizing conversations while preserving essential information.
        
        Your task is to:
        1. Identify active tasks, ongoing work, and action items
        2. Extract key decisions, conclusions, and important constraints
        3. Preserve file references, code snippets, and technical details
        4. Maintain conversation flow and timeline coherence
        5. Reduce token usage while retaining critical context
        
        Focus on maintaining conversational continuity and ensuring that
        the user can continue the discussion without losing important context.
        """
        
        return Agent(
            name="context_compactor",
            instructions=system_prompt,
            model=self.model,
            tools=[
                self._create_analyze_tool(),
                self._create_summarize_tool(),
                self._create_validate_tool()
            ]
        )
        
    def compact_conversation(self, conversation_history: List[Dict], target_tokens: int) -> str:
        """Main method to compact conversation using the specialized agent."""
        pass
        
    def compact_with_instructions(self, conversation_history: List[Dict], 
                                instructions: str, target_tokens: int = None) -> str:
        """Compact conversation with user-provided natural language instructions.
        
        Args:
            conversation_history: List of conversation messages
            instructions: Natural language instructions for what to preserve/remove
            target_tokens: Optional target token count
            
        Returns:
            Compacted conversation summary following user instructions
        """
        # Create instruction-aware agent
        instruction_agent = self._create_enhanced_agent(instructions)
        
        # Execute compaction with instructions
        result = instruction_agent.run(
            f"Compact the following conversation according to the instructions provided:\n\n"
            f"CONVERSATION:\n{self._format_conversation(conversation_history)}\n\n"
            f"TARGET TOKENS: {target_tokens or 'reduce significantly while preserving context'}"
        )
        
        return result.value
        
    def _create_enhanced_agent(self, user_instructions: str) -> Agent:
        """Create agent with user-specific instructions for compaction."""
        enhanced_system_prompt = f"""
        You are a Context Compaction Agent with specific user instructions:
        
        USER INSTRUCTIONS: {user_instructions}
        
        Follow these instructions precisely while:
        1. Maintaining conversation coherence and flow
        2. Preserving essential technical details and code
        3. Keeping the conversation natural and readable
        4. Ensuring the user can continue their work seamlessly
        5. Removing unnecessary repetition and verbosity
        
        Pay special attention to:
        - Topics the user wants to focus on
        - Content they explicitly want to avoid
        - Temporal references (earlier discussions vs. current work)
        - Specific files, features, or tasks mentioned
        - Action items, decisions, and next steps
        
        The goal is to create a compact version that allows the conversation to continue
        naturally while respecting all the user's specific instructions.
        """
        
        return Agent(
            name="instruction_aware_compactor",
            instructions=enhanced_system_prompt,
            model=self.model,
            tools=[
                self._create_instruction_aware_analyze_tool(),
                self._create_instruction_aware_summarize_tool()
            ]
        )
```

#### 2.2 Context Analyzer
```python
class ContextAnalyzer:
    """
    Analyzes conversation context to identify:
    - Active tasks and ongoing work
    - Key decisions and conclusions
    - Important file references and changes
    - Critical user preferences and constraints
    - Tool usage patterns and results
    """
    def extract_key_elements(self, conversation: List[Dict]) -> ContextElements:
        """Extract structured key elements from conversation."""
        
    def calculate_importance_score(self, element: Any) -> float:
        """Score elements by importance for retention."""
```

#### 2.3 Preview Modal Manager
```python
class CompactPreviewModal:
    """
    Manages the Snacks.win-based preview interface.
    """
    def __init__(self, nvim, snacks_available: bool):
        self.nvim = nvim
        self.snacks_available = snacks_available
        
    def show_preview(self, original_context: str, compacted_summary: str) -> bool:
        """Show preview modal and return user approval status."""
        
    def setup_keybindings(self, win: snacks.win):
        """Setup keybindings for accept/reject/modify actions."""
```

### 4.4 Slash Command Integration
```python
# Extension to existing command system
class CompactSlashCommand(SlashCommand):
    """Handles /compact slash command execution."""
    
    def execute(self, args: List[str], context: Dict) -> None:
        """Execute compact command with instructions or optional parameters."""
        # Parse command into parameters vs natural language instructions
        params, instructions = self._parse_compact_command(args)
        
        if instructions:
            # Use natural language instructions for specialized compaction
            return self._execute_with_instructions(instructions, params, context)
        else:
            # Use standard parameter-based compaction
            return self._execute_with_params(params, context)
            
    def _execute_with_instructions(self, instructions: str, params: Dict, context: Dict) -> None:
        """Execute compaction with natural language instructions."""
        # Infer token target from instructions or use explicit parameter
        target_tokens = params.get('--tokens') or self._infer_token_target(instructions)
        current_context = context['conversation_history']
        
        # Use instruction-aware compaction
        summary = self.compact_agent.compact_with_instructions(
            current_context, instructions, target_tokens
        )
        
        # Show preview and apply if approved
        if self.preview_modal.show_preview(current_context, summary):
            self._apply_compacted_context(summary)
            
    def _infer_token_target(self, instructions: str) -> int:
        """Intelligently infer target token count from natural language instructions.
        
        Examples:
        - "compact aggressively" -> 30% of current tokens
        - "reduce significantly" -> 50% of current tokens  
        - "light compaction" -> 85% of current tokens
        - "around 2000 tokens" -> 2000 tokens
        """
        instructions_lower = instructions.lower()
        current_tokens = self._get_current_token_count()
        
        # Aggressive compaction indicators
        if any(word in instructions_lower for word in [
            'aggressive', 'heavily', 'drastically', 'severely', 'major', 'significant'
        ]):
            return int(current_tokens * 0.3)
            
        # Moderate compaction indicators
        elif any(word in instructions_lower for word in [
            'moderately', 'somewhat', 'a bit', 'significantly', 'substantially'
        ]):
            return int(current_tokens * 0.5)
            
        # Light compaction indicators
        elif any(word in instructions_lower for word in [
            'light', 'lightly', 'minimal', 'small', 'slightly', 'gently'
        ]):
            return int(current_tokens * 0.85)
            
        # Look for specific number patterns
        import re
        token_patterns = [
            r'(\d+)\s*tokens?',
            r'(\d+)\s*token', 
            r'around\s*(\d+)',
            r'about\s*(\d+)',
            r'~(\d+)',
            r'approximately\s*(\d+)'
        ]
        
        for pattern in token_patterns:
            match = re.search(pattern, instructions_lower)
            if match:
                target = int(match.group(1))
                # Sanity check the target
                if 100 <= target <= current_tokens:
                    return target
                    
        # Default based on instruction complexity
        instruction_words = len(instructions.split())
        if instruction_words > 25:  # Complex instructions suggest specific needs
            return int(current_tokens * 0.4)
        elif instruction_words > 15:  # Moderately detailed
            return int(current_tokens * 0.5)
        else:  # Simple instructions
            return int(current_tokens * 0.6)
            
    def _parse_compact_command(self, args: List[str]) -> Tuple[Dict, str]:
        """Parse command into parameters and natural language instructions."""
        # Look for parameters like --tokens, --strategy, etc.
        # Everything else becomes natural language instructions
        pass
        
    def _execute_with_instructions(self, instructions: str, context: Dict) -> None:
        """Execute compaction with user-provided natural language instructions."""
        pass
```

#### Natural Language Instruction Support

The `/compact` command supports **natural language instructions** to provide precise control over what should be preserved or removed:

**Examples:**
```bash
# Focus on specific ongoing work
/compact the conversation so we can continue working on user authentication, avoid any of the code exploration, and questions about the homepage we had at the beginning

# Remove debugging session details
/compact keep the current API implementation discussion but remove all the debugging and error troubleshooting from earlier

# Focus on decisions made
/compact focus on the decisions we made about the database schema and remove the initial brainstorming

# Preserve specific topics
/compact keep all mentions of the payment system and user permissions, remove the UI design discussions

# Target specific context
/compact summarize everything but preserve the context about the authentication flow we were implementing
```

#### Instruction Processing

The CompactAgent processes instructions by:
1. **Parsing intent**: Understand what to focus on vs. what to remove
2. **Topic identification**: Recognize specific subjects (authentication, homepage, etc.)
3. **Temporal awareness**: Distinguish between "earlier discussions" vs. "current work"
4. **Action prioritization**: Identify and preserve ongoing tasks and next steps
5. **Selective filtering**: Apply user-specified filters to the compaction process

#### Enhanced Agent Instructions

When natural language instructions are provided, the CompactAgent's system prompt is enhanced:

```python
def _create_enhanced_agent(self, user_instructions: str) -> Agent:
    enhanced_system_prompt = f"""
    You are a Context Compaction Agent with specific user instructions:
    
    USER INSTRUCTIONS: {user_instructions}
    
    Follow these instructions precisely while:
    1. Maintaining conversation coherence
    2. Preserving essential technical details
    3. Keeping the conversation flow natural
    4. Ensuring the user can continue their work seamlessly
    
    Pay special attention to:
    - Topics the user wants to focus on
    - Content they want to avoid
    - Temporal references (earlier vs. current)
    - Specific files, features, or tasks mentioned
    """
    
    return Agent(
        name="context_compactor",
        instructions=enhanced_system_prompt,
        model=self.model,
        tools=[self._create_instruction_aware_tools()]
    )
```

## 3. Process Flow

### 3.1 Command Trigger Flow
```
User types "/compact" in AgentPrompt
    ↓
Slash command parser detects "/compact"
    ↓
Parse optional arguments (e.g., target_tokens=2000)
    ↓
Trigger CompactAgent workflow
```

### 3.2 Analysis Phase
```
Extract current conversation history from AgentContent buffer
    ↓
ContextAnalyzer.analyze_context() processes conversation:
    - Identify active tasks and work in progress
    - Extract key decisions and conclusions  
    - Catalog important file references
    - Note critical constraints and preferences
    - Track tool usage and results
    ↓
Generate context analysis report with importance scores
```

### 3.3 Summarization Phase
```
CompactAgent.generate_summary():
    - Use context analysis to guide summarization
    - Apply targeted token reduction strategy
    - Preserve conversation flow and timeline
    - Maintain references to files and tools used
    - Ensure action items and next steps are retained
    ↓
Generate initial compacted summary
```

### 3.4 Preview and Approval Phase
```
CompactPreviewModal.show_preview():
    - Create Snacks.win with side-by-side comparison
    - Left: Original context statistics
    - Right: Compacted summary
    - Bottom: Action buttons and keybinding hints
    
User interactions:
    - <Enter> or y: Accept and apply summary
    - <Esc> or n: Cancel compaction
    - e: Edit summary in temporary buffer
    - r: Regenerate with different parameters
```

### 3.5 Application Phase
```
If user accepts:
    - Replace AgentContent buffer with compacted summary
    - Update conversation history in plugin state
    - Add metadata about compaction (timestamp, tokens saved)
    - Show brief success message
    
If user edits:
    - Apply user-modified summary
    - Continue with application phase
    
If user cancels:
    - Clean up preview window
    - Return to normal conversation
```

## 4. Integration Points

### 4.1 Plugin Integration (`plugin.py`)
```python
class AgentPlugin:
    def __init__(self, nvim):
        # Existing initialization
        self._setup_compact_agent()  # New initialization method
        
    def _setup_compact_agent(self):
        """Initialize the specialized CompactAgent with model configuration."""
        compact_model = self._get_compact_model()
        self.compact_agent = CompactAgent(model=compact_model, logger=self.logger)
        self.context_analyzer = ContextAnalyzer(self.logger)
        self.preview_modal = CompactPreviewModal(nvim, self._check_snacks())
        
    def _get_compact_model(self) -> str:
        """Get model for compact agent, with environment variable override."""
        custom_model = os.environ.get('AGENT_COMPACT_MODEL')
        if custom_model:
            self.logger.info(f"Using custom compact model from AGENT_COMPACT_MODEL: {custom_model}")
            return custom_model
        # Use the same model as the main agent by default
        return self.model
        
    def _parse_compact_command(self, args: List[str]) -> Tuple[Dict, str]:
        """Parse command into parameters and natural language instructions."""
        # Convert args to string for natural language processing
        args_str = ' '.join(args) if args else ''
        
        # Extract flags and parameters (starting with --)
        param_pattern = r'(--\w+)(?:=([^\s]+))?'  # Match --flag or --flag=value
        params = {}
        remaining_text = args_str
        
        for match in re.finditer(param_pattern, args_str):
            flag = match.group(1)
            value = match.group(2)  # May be None for boolean flags
            params[flag] = value if value is not None else True
            
        # Remove parameter patterns to get instructions
        instructions = re.sub(param_pattern, '', args_str).strip()
        
        return params, instructions
        
    def handle_compact_command(self, args: List[str]) -> None:
        """Handle /compact slash command using the specialized agent."""
        target_tokens = self._parse_compact_args(args)
        current_context = self._get_conversation_context()
        
        # Parse for natural language instructions
        params, instructions = self._parse_compact_command(args)
        
        if instructions:
            # Use instruction-aware compaction
            summary = self.compact_agent.compact_with_instructions(
                current_context, instructions, target_tokens
            )
        else:
            # Use standard parameter-based compaction
            summary = self.compact_agent.compact_conversation(current_context, target_tokens)
        
        # Preview phase
        if self.preview_modal.show_preview(current_context, summary):
            # Apply compaction
            self._apply_compacted_context(summary)
```

### 4.2 Slash Command System Integration
Extend existing slash command infrastructure:
- Add `/compact` to command registry
- Support optional parameters (target token count, compression ratio)
- Integrate with existing command parsing and help system

### 4.3 Buffer Management Integration (`buffers.py`)
```python
class BufferManager:
    def get_conversation_context(self) -> List[Dict]:
        """Extract current conversation context from AgentContent."""
        
    def apply_compacted_context(self, summary: str) -> None:
        """Replace buffer content with compacted summary."""
        
    def add_compaction_metadata(self, metadata: Dict) -> None:
        """Add metadata about compaction to buffer."""
```

### 4.4 Snacks.win Integration
```lua
-- Lua helper for preview modal
local M = {}

function M.create_compact_preview(original, summary)
  local snacks = require("snacks")
  
  return snacks.win({
    title = "Context Compaction Preview",
    width = 0.8,
    height = 0.8,
    border = "rounded",
    style = "split",
    position = "float",
    keys = {
      ["<Enter>"] = "accept",
      ["y"] = "accept", 
      ["<Esc>"] = "close",
      ["n"] = "close",
      ["e"] = "edit",
      ["r"] = "regenerate",
      ["q"] = "close"
    },
    actions = {
      accept = function(win) 
        -- Signal acceptance to Python layer
        vim.g.agent_compact_decision = "accept"
        win:close()
      end,
      edit = function(win)
        -- Open summary in editable buffer
        vim.g.agent_compact_decision = "edit"
        win:close()
      end,
      regenerate = function(win)
        vim.g.agent_compact_decision = "regenerate" 
        win:close()
      end
    }
  })
end
```

## 5. Implementation Challenges and Considerations

### 5.1 Technical Challenges

#### Context Analysis Complexity
- **Challenge**: Accurately identifying what information is "essential" vs. "dispensable"
- **Approach**: 
  - Use multiple heuristics (recency, frequency, user emphasis markers)
  - Implement importance scoring algorithms
  - Allow user customization of what to prioritize

#### Token Count Accuracy
- **Challenge**: Precisely calculating token counts for different models
- **Approach**:
  - Use OpenAI's tiktoken library for accurate counting
  - Implement fallback estimation methods
  - Account for both input and output tokens

#### State Consistency
- **Challenge**: Maintaining plugin state consistency after compaction
- **Approach**:
  - Carefully update conversation history
  - Preserve tool context and buffer states
  - Ensure token tracking remains accurate

### 5.2 User Experience Considerations

#### Preview Interface Design
- **Challenge**: Clear visualization of what's being preserved vs. removed
- **Approach**:
  - Side-by-side comparison with statistics
  - Highlighting of preserved key elements
  - Clear indicators of token savings

#### User Trust and Control
- **Challenge**: Building trust in automated summarization
- **Approach**:
  - Always require explicit user approval
  - Provide transparency in the process
  - Allow easy rollback if needed
  - Show before/after statistics

#### Performance Considerations
- **Challenge**: Avoiding delays in conversation flow
- **Approach**:
  - Implement caching of analysis results
  - Use streaming for large conversations
  - Provide progress indicators

### 5.3 Model Configuration

#### Default and Custom Model Support
- **Default Model**: Uses the same model as the main agent for consistency
- **Environment Variable**: `AGENT_COMPACT_MODEL` allows users to specify a different model
- **Model Selection Logic**:
  ```python
  def get_compact_model() -> str:
      """Get model for compact agent, with environment variable override."""
      custom_model = os.environ.get('AGENT_COMPACT_MODEL')
      if custom_model:
          logger.info(f"Using custom compact model: {custom_model}")
          return custom_model
      return self.model  # Use default agent model
  ```

#### Model Compatibility
- **Tokenization**: Use appropriate tokenizer for the selected model
- **Context Limits**: Respect model-specific context windows
- **Performance**: Consider using faster/cheaper models for summarization tasks

### 5.4 Integration Challenges

#### Snacks.win Dependency
- **Challenge**: Graceful handling when Snacks.nvim is not available
- **Approach**:
  - Implement fallback using native Neovim floating windows
  - Provide clear error messages
  - Make Snacks.nvim an optional dependency

#### Model Compatibility
- **Challenge**: Working with different AI models and their tokenization
- **Approach**:
  - Support multiple tokenization methods
  - Allow model-specific configurations
  - Implement adaptive strategies

#### Conversation Continuity
- **Challenge**: Ensuring conversations remain coherent after compaction
- **Approach**:
  - Preserve conversation flow and context links
  - Maintain thread structure if present
  - Add transition markers indicating where compaction occurred

### 5.4 Future Enhancements

#### Advanced Summarization Features
- **Adaptive compaction**: Learn from user acceptance patterns
- **Topic-based grouping**: Organize summary by conversation topics
- **Smart extraction**: Preserve code snippets and technical details

#### Integration Enhancements  
- **Automatic triggering**: Suggest compaction when tokens approach limits
- **Batch processing**: Compact multiple related conversations
- **Export/import**: Save and restore compacted contexts

#### UI/UX Improvements
- **Diff visualization**: Show detailed changes between original and compacted
- **Customizable presets**: User-defined compaction strategies
- **Analytics dashboard**: Track token savings and usage patterns

## 6. Implementation Phases

### Phase 1: Core Functionality
- Implement CompactAgent and ContextAnalyzer classes
- Add basic slash command integration
- Create initial buffer management functions

### Phase 2: Preview Interface  
- Implement Snacks.win preview modal
- Add Lua helper functions
- Create keybinding system for user actions

### Phase 3: Integration and Testing
- Integrate with existing plugin architecture
- Add comprehensive error handling
- Implement fallback mechanisms

### Phase 4: Polish and Documentation
- Add user documentation and help
- Implement configuration options
- Performance optimization and testing

## 8. Usage Examples and Command Patterns

### Basic Usage
```bash
# Simple compaction (token target inferred from context)
/compact

# Natural language with implicit token targets
/compact heavily compress this conversation  # infers aggressive reduction
/compact lightly reduce the context         # infers light reduction

# Use custom model for the compact agent
AGENT_COMPACT_MODEL=gpt-4o-mini nvim  # Then use /compact
```

### Token Target Inference

The system intelligently infers token targets from both natural language and conversation context:

**From Language:**
- "aggressive/heavily/drastically" → ~30% of current
- "significantly/moderately" → ~50% of current
- "lightly/minimally/gently" → ~85% of current
- "around/2000 tokens/about 1500" → exact number

**From Context:**
- Small conversations (< 2K tokens) → gentle compaction
- Medium conversations (2K-5K tokens) → moderate compaction
- Large conversations (5K-10K tokens) → standard compaction
- Very large conversations (> 10K tokens) → aggressive compaction

### Natural Language Instructions (Token Inference Examples)
```bash
# The agent automatically infers token targets from your language:

# Aggressive compaction (infers ~30% reduction)
/compact aggressively compact this conversation
/compact heavily reduce the context
/compact we need to drastically cut down the conversation size

# Moderate compaction (infers ~50% reduction) 
/compact compress this conversation significantly
/compact we need to substantially reduce the context
/compact moderately compact this discussion

# Light compaction (infers ~85% reduction)
/compact lightly compress this conversation
/compact do a minimal compaction
/compact gently reduce the context

# Explicit token targets (exact number)
/compact reduce to around 2000 tokens
/compact compact to about 1500 tokens
/compact get it down to ~3000 tokens

# Focus on specific work (infers target based on complexity)
/compact focus on the authentication flow we are working on
/compact keep the current API implementation discussion but remove the debugging
/compact preserve the payment system context, compress everything else
```

### Natural Language Instructions
```bash
# Focus on specific ongoing work
/compact the conversation so we can continue working on user authentication, avoid any of the code exploration, and questions about the homepage we had at the beginning

# Remove debugging session details  
/compact keep the current API implementation discussion but remove all the debugging and error troubleshooting from earlier

# Focus on decisions made
/compact focus on the decisions we made about the database schema and remove the initial brainstorming

# Preserve specific topics
/compact keep all mentions of the payment system and user permissions, remove the UI design discussions

# Target specific context
/compact summarize everything but preserve the context about the authentication flow we were implementing

# Remove specific types of content
/compact remove all the initial setup and installation steps, keep only the current feature development

# Time-based filtering
/compact keep only the discussions from the last hour, remove everything else

# Project-specific focus
/compact preserve everything related to the user authentication module, compress all other discussions

# Remove off-topic conversations
/compact remove the sidebar about coffee preferences and keep only the programming discussion

# Mixed instructions with parameters
/compact --tokens=3000 focus on the API design decisions, remove the implementation details and debugging
```

### Advanced Patterns
```bash
# Multiple focus areas
/compact preserve discussions about database design, API contracts, and user authentication. Remove the CSS styling and frontend layout conversations.

# Preserve specific file contexts
/compact keep all mentions of src/auth.py, src/database.py, and the user model, compress everything else

# Maintain action items
/compact ensure all TODO items, next steps, and action items are preserved, compress the rest of the conversation

# Remove trial and error
/compact remove all the failed attempts and dead ends, keep only the working solutions and decisions we made

# Prepare for handoff
/compact summarize this session for someone else to understand what we built, remove the exploratory process and focus on the final implementation
```

### Strategy-Based Instructions
```bash
# Technical discussions
/compact preserve all code snippets, commands, and technical decisions, remove explanations and background

# Design discussions  
/compact keep the design decisions and reasoning, remove the implementation details

# Debugging sessions
/compact preserve the error messages, solutions, and fixes, remove the failed attempts

# Planning sessions
/compact keep all action items, timelines, and decisions, remove the brainstorming and exploratory discussion
```

## 9. Success Metrics

- **Token Reduction**: Achieve 40-70% token reduction while preserving 90%+ of essential information
- **User Acceptance**: >80% of generated summaries accepted without modification  
- **Performance**: Preview generation and display under 2 seconds for typical conversations
- **Reliability**: <1% failure rate with graceful error recovery
- **Token Inference Accuracy**: >85% of inferred token targets match user intent without explicit specification
- **Natural Language Understanding**: >90% of user instructions correctly interpreted and applied
