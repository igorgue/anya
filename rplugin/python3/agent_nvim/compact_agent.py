"""CompactAgent implementation for context compaction and summarization."""

import re
import os
import logging

# Try to import typing, with fallback for older Python versions
try:
    from typing import List, Dict, Any, Optional, Tuple
except ImportError:
    # Fallback for older Python versions
    List = list
    Dict = dict
    Any = object
    Optional = type
    Tuple = tuple


class CompactAgent:
    """
    Specialized agent for context compaction and summarization.

    This is a NEW AGENT created with OpenAI Agents SDK, specifically
    designed for summarization tasks with its own system prompt and
    tools focused on context analysis and compaction.
    """

    def __init__(self, model: str, logger: logging.Logger):
        self.model = model
        self.logger = logger
        self.agent = self._create_compact_agent()

    def _run_async_safely(self, coro):
        """Run async code safely, handling existing event loops.

        If an event loop is already running (common in Neovim plugins),
        run the coroutine in a separate thread with its own event loop.
        """
        import asyncio
        import concurrent.futures

        try:
            # Try to get the running loop
            asyncio.get_running_loop()
            # If we get here, a loop is already running
            self.logger.debug(
                "Event loop detected, running async code in separate thread"
            )
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(asyncio.run, coro)
                return future.result()
        except RuntimeError:
            # No event loop running, use asyncio.run normally
            return asyncio.run(coro)

    def _create_compact_agent(self):
        """Create specialized summarization agent with custom system prompt."""
        try:
            from agents import Agent, function_tool

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
                    self._create_validate_tool(),
                ],
            )
        except ImportError:
            self.logger.error("Could not import Agent from agents package")
            return None

    def _create_analyze_tool(self):
        """Create tool for analyzing conversation context."""
        try:
            from agents import function_tool

            def analyze_context(conversation_text: str) -> str:
                """
                Analyze conversation context to identify key elements.

                Args:
                    conversation_text: The conversation history to analyze

                Returns:
                    Analysis of key elements, active tasks, and important context
                """
                # Simple analysis - in a real implementation this would be more sophisticated
                lines = conversation_text.split("\n")

                # Look for indicators of active work
                active_tasks = []
                file_references = []
                decisions = []

                for line in lines:
                    # Look for file references
                    if "@" in line and any(
                        ext in line for ext in [".py", ".js", ".lua", ".md", ".txt"]
                    ):
                        file_references.append(line.strip())

                    # Look for action items
                    if any(
                        word in line.lower()
                        for word in ["todo:", "fix:", "implement", "add", "create"]
                    ):
                        active_tasks.append(line.strip())

                    # Look for decisions
                    if any(
                        word in line.lower()
                        for word in ["decided", "agreed", "conclusion", "final"]
                    ):
                        decisions.append(line.strip())

                analysis = []
                if active_tasks:
                    analysis.append(f"Active Tasks ({len(active_tasks)}):")
                    analysis.extend(f"  - {task}" for task in active_tasks[:5])

                if file_references:
                    analysis.append(f"\nFile References ({len(file_references)}):")
                    analysis.extend(f"  - {ref}" for ref in file_references[:5])

                if decisions:
                    analysis.append(f"\nKey Decisions ({len(decisions)}):")
                    analysis.extend(f"  - {decision}" for decision in decisions[:3])

                return (
                    "\n".join(analysis)
                    if analysis
                    else "No specific key elements identified."
                )

            return function_tool(analyze_context)
        except ImportError:
            self.logger.debug("Could not import function_tool from agents")
            return None
        except Exception as e:
            self.logger.error(f"Error creating analyze tool: {e}")
            return None
            return None

    def _create_summarize_tool(self):
        """Create tool for summarizing content."""
        try:
            from agents import function_tool

            def create_summary(content: str, target_ratio: float = 0.5) -> str:
                """
                Create a summary of the provided content.

                Args:
                    content: Content to summarize
                    target_ratio: Target compression ratio (0.0 to 1.0)

                Returns:
                    Summarized content
                """
                # Simple summarization - in real implementation would be more sophisticated
                lines = content.split("\n")
                target_lines = int(len(lines) * target_ratio)

                # Keep first and last portions, plus some middle content
                if len(lines) <= target_lines:
                    return content

                first_part = lines[: target_lines // 3]
                last_part = lines[-target_lines // 3 :]
                middle_start = len(lines) // 2 - target_lines // 6
                middle_end = middle_start + target_lines // 3
                middle_part = lines[middle_start:middle_end]

                summary_lines = (
                    first_part
                    + ["\n... [content compacted] ...\n"]
                    + middle_part
                    + ["\n... [content compacted] ...\n"]
                    + last_part
                )
                return "\n".join(summary_lines)

            return function_tool(create_summary)
        except ImportError:
            self.logger.debug("Could not import function_tool from agents")
            return None
        except Exception as e:
            self.logger.error(f"Error creating summarize tool: {e}")
            return None

    def _create_validate_tool(self):
        """Create tool for validating summary quality."""
        try:
            from agents import function_tool

            def validate_summary(original: str, summary: str) -> str:
                """
                Validate that summary preserves essential information.

                Args:
                    original: Original conversation content
                    summary: Generated summary

                Returns:
                    Validation result with recommendations
                """
                # Check for preservation of key elements
                original_files = set(re.findall(r"@\w+\.\w+", original))
                summary_files = set(re.findall(r"@\w+\.\w+", summary))

                missing_files = original_files - summary_files
                validation = []

                if missing_files:
                    validation.append(
                        f"  Missing file references: {', '.join(missing_files)}"
                    )
                else:
                    validation.append("**All file references preserved**")

                # Check length reduction
                reduction = 1 - (len(summary) / len(original))
                validation.append(f"Size reduction: {reduction:.1%}")

                return "\n".join(validation)

            return function_tool(validate_summary)
        except ImportError:
            self.logger.debug("Could not import function_tool from agents")
            return None
        except Exception as e:
            self.logger.error(f"Error creating validate tool: {e}")
            return None

    def compact_conversation(
        self, conversation_history: List[Dict], target_tokens: Optional[int] = None
    ) -> tuple:
        """Main method to compact conversation using the specialized agent.

        Returns:
            Tuple of (summary_text, compacted_conversation_history)
        """
        if not self.agent:
            self.logger.warning(
                "Compact agent not available, using fallback compaction"
            )
            return self._fallback_compaction(conversation_history, target_tokens)

        try:
            import asyncio
            from agents import Runner

            # Format conversation for agent
            conversation_text = self._format_conversation(conversation_history)

            # Determine target
            if target_tokens:
                target_prompt = (
                    f"Compact the conversation to approximately {target_tokens} tokens."
                )
            else:
                target_prompt = (
                    "Compact the conversation while preserving essential context."
                )

            # Run the agent using Runner
            async def run_compact():
                from .model_provider import get_custom_run_config

                run_kwargs = {
                    "starting_agent": self.agent,
                    "input": f"{target_prompt}\n\nCONVERSATION:\n{conversation_text}\n\nProvide a concise summary that preserves key decisions and context.",
                }
                custom_run_config = get_custom_run_config()
                if custom_run_config:
                    run_kwargs["run_config"] = custom_run_config

                result = await Runner.run(**run_kwargs)
                return (
                    str(result.final_output)
                    if hasattr(result, "final_output")
                    else str(result)
                )

            # Run async code, handling case where event loop is already running
            summary_text = self._run_async_safely(run_compact())

            # Convert summary back to conversation history format
            compacted_history = self._summary_to_conversation_history(
                summary_text, conversation_history
            )

            return summary_text, compacted_history

        except Exception as e:
            self.logger.error(f"Error in compact_conversation: {e}")
            self.logger.info("Falling back to simple compaction method")
            return self._fallback_compaction(conversation_history, target_tokens)

    def _fallback_compaction(
        self, conversation_history: List[Dict], target_tokens: Optional[int] = None
    ) -> tuple:
        """Fallback compaction method when the agent is not available.

        Returns:
            Tuple of (summary_text, compacted_conversation_history)
        """
        try:
            # Format conversation
            conversation_text = self._format_conversation(conversation_history)
            lines = conversation_text.split("\n")

            # Simple compaction logic
            if target_tokens:
                # Rough estimate: 4 chars per token, so target_chars = target_tokens * 4
                target_chars = target_tokens * 4

                # Take first and last parts, keep important content
                if len(conversation_text) <= target_chars:
                    summary = conversation_text
                    compacted_history = self._summary_to_conversation_history(
                        summary, conversation_history
                    )
                    return summary, compacted_history

                # Simple strategy: keep first 30%, middle 20%, last 30%
                first_part_end = int(len(lines) * 0.3)
                middle_start = int(len(lines) * 0.4)
                middle_end = int(len(lines) * 0.6)
                last_part_start = int(len(lines) * 0.7)

                compacted_lines = []

                # Add first part
                compacted_lines.extend(lines[:first_part_end])

                # Add important markers
                compacted_lines.append("")
                compacted_lines.append("... [content compacted for brevity] ...")
                compacted_lines.append("")

                # Add middle part with important content
                compacted_lines.extend(lines[middle_start:middle_end])

                # Add important markers
                compacted_lines.append("")
                compacted_lines.append("... [additional content compacted] ...")
                compacted_lines.append("")

                # Add last part
                compacted_lines.extend(lines[last_part_start:])

                compacted_text = "\n".join(compacted_lines)

                # If still too long, truncate more aggressively
                while len(compacted_text) > target_chars and len(compacted_lines) > 20:
                    # Remove lines from the middle
                    mid = len(compacted_lines) // 2
                    del compacted_lines[mid - 2 : mid + 2]  # Remove 4 lines from middle
                    compacted_text = "\n".join(compacted_lines)

                compacted_history = self._summary_to_conversation_history(
                    compacted_text, conversation_history
                )
                return compacted_text, compacted_history
            else:
                # Default: reduce to about 60% of original
                target_lines = max(10, int(len(lines) * 0.6))

                # Keep first half and last quarter
                first_part = lines[: target_lines // 2]
                last_part = lines[-target_lines // 4 :]

                compacted_text = "\n".join(
                    first_part + ["\n... [content compacted] ...\n"] + last_part
                )
                compacted_history = self._summary_to_conversation_history(
                    compacted_text, conversation_history
                )
                return compacted_text, compacted_history

        except Exception as e:
            self.logger.error(f"Error in fallback compaction: {e}")
            # Ultimate fallback: return a simple summary
            summary = f"## Compacted Conversation\n\nThe conversation has been compacted to reduce token usage.\nOriginal conversation had {len(conversation_history)} messages.\n\n*Note: Advanced compaction features require OpenAI agents SDK.*"
            compacted_history = self._summary_to_conversation_history(
                summary, conversation_history
            )
            return summary, compacted_history

    def compact_with_instructions(
        self,
        conversation_history: List[Dict],
        instructions: str,
        target_tokens: Optional[int] = None,
    ) -> tuple:
        """Compact conversation with user-provided natural language instructions.

        Args:
            conversation_history: List of conversation messages
            instructions: Natural language instructions for what to preserve/remove
            target_tokens: Optional target token count

        Returns:
            Tuple of (summary_text, compacted_conversation_history)
        """
        # Check if instruction-aware agent is available
        instruction_agent = self._create_enhanced_agent(instructions)

        if not instruction_agent:
            self.logger.warning(
                "Instruction-aware agent not available, using enhanced fallback"
            )
            return self._fallback_compaction_with_instructions(
                conversation_history, instructions, target_tokens
            )

        try:
            import asyncio
            from agents import Runner

            # Format conversation for agent
            conversation_text = self._format_conversation(conversation_history)

            # Execute compaction with instructions
            target_text = f" (target: ~{target_tokens} tokens)" if target_tokens else ""
            prompt = f"Compact the following conversation according to the instructions provided{target_text}:\n\n"
            prompt += f"CONVERSATION:\n{conversation_text}\n\n"
            prompt += f"INSTRUCTIONS:\n{instructions}\n\nProvide a concise summary that follows the user's instructions."

            async def run_with_instructions():
                from .model_provider import get_custom_run_config

                run_kwargs = {
                    "starting_agent": instruction_agent,
                    "input": prompt,
                }
                custom_run_config = get_custom_run_config()
                if custom_run_config:
                    run_kwargs["run_config"] = custom_run_config

                result = await Runner.run(**run_kwargs)
                return (
                    str(result.final_output)
                    if hasattr(result, "final_output")
                    else str(result)
                )

            summary_text = self._run_async_safely(run_with_instructions())

            # Convert summary back to conversation history format
            compacted_history = self._summary_to_conversation_history(
                summary_text, conversation_history
            )

            return summary_text, compacted_history

        except Exception as e:
            self.logger.error(f"Error in compact_with_instructions: {e}")
            self.logger.info("Falling back to enhanced simple compaction method")
            return self._fallback_compaction_with_instructions(
                conversation_history, instructions, target_tokens
            )

    def _fallback_compaction_with_instructions(
        self,
        conversation_history: List[Dict],
        instructions: str,
        target_tokens: Optional[int] = None,
    ) -> tuple:
        """Fallback compaction with instructions when agent is not available.

        Returns:
            Tuple of (summary_text, compacted_conversation_history)
        """
        try:
            # Parse instructions for content filtering
            instructions_lower = instructions.lower()

            # Format conversation
            conversation_text = self._format_conversation(conversation_history)

            # Identify focus topics (keep these)
            keep_keywords = []
            avoid_keywords = []

            # Extract topics to keep
            keep_patterns = [
                r"keep\s+(?:all\s+)?(?:mentions\s+of\s+)?([^,\.]+)",
                r"preserve\s+(?:all\s+)?(?:mentions\s+of\s+)?([^,\.]+)",
                r"focus\s+on\s+(?:the\s+)?([^,\.]+)",
            ]

            import re

            for pattern in keep_patterns:
                matches = re.findall(pattern, instructions_lower)
                keep_keywords.extend(matches)

            # Extract topics to avoid
            avoid_patterns = [
                r"remove\s+(?:all\s+)?(?:mentions\s+of\s+)?([^,\.]+)",
                r"avoid\s+(?:all\s+)?(?:mentions\s+of\s+)?([^,\.]+)",
                r"exclude\s+(?:all\s+)?(?:mentions\s+of\s+)?([^,\.]+)",
            ]

            for pattern in avoid_patterns:
                matches = re.findall(pattern, instructions_lower)
                avoid_keywords.extend(matches)

            # Filter lines based on instructions
            lines = conversation_text.split("\n")
            filtered_lines = []

            for line in lines:
                line_lower = line.lower().strip()
                should_keep = True

                # Check if line contains topics to avoid
                for avoid_topic in avoid_keywords:
                    if avoid_topic.strip() and avoid_topic in line_lower:
                        should_keep = False
                        break

                # If no keep keywords, keep the line (unless avoided)
                if keep_keywords:
                    should_keep = False
                    # Only keep if it contains at least one keep keyword
                    for keep_topic in keep_keywords:
                        if keep_topic.strip() and keep_topic in line_lower:
                            should_keep = True
                            break

                # Always keep important structural elements
                if any(
                    marker in line
                    for marker in ["## ", "### ", "TODO:", "FIXME:", "@", "```"]
                ):
                    should_keep = True

                if should_keep:
                    filtered_lines.append(line)

            # Now apply size reduction if needed
            filtered_text = "\n".join(filtered_lines)

            if target_tokens:
                target_chars = target_tokens * 4
                if len(filtered_text) > target_chars:
                    # Truncate to meet target
                    filtered_lines = filtered_lines[
                        : int(target_chars / len("\n".join(filtered_lines[:1]) + "\n"))
                    ]

            # Add metadata about the compaction
            result_lines = [
                "## Compacted Conversation",
                f"*Compacted based on instructions: {instructions}*",
                "",
                "### Preserved Content",
                "",
            ]

            result_lines.extend(filtered_lines)

            if not filtered_lines:
                result_lines.append(
                    "*No content matched the specified instructions. Original conversation preserved in compacted form.*"
                )
                result_lines.append(
                    conversation_text[:1000] + "..."
                    if len(conversation_text) > 1000
                    else conversation_text
                )

            result_lines.extend(
                [
                    "",
                    "---",
                    "*Note: Advanced AI-powered compaction requires OpenAI agents SDK*",
                    "",
                ]
            )

            summary = "\n".join(result_lines)
            compacted_history = self._summary_to_conversation_history(
                summary, conversation_history
            )
            return summary, compacted_history

        except Exception as e:
            self.logger.error(f"Error in fallback compaction with instructions: {e}")
            # Ultimate fallback
            summary = f"## Compacted Conversation\n\nFailed to apply specific instructions. Simple compaction applied.\n\nInstructions: {instructions}\n\nOriginal messages: {len(conversation_history)}"
            compacted_history = self._summary_to_conversation_history(
                summary, conversation_history
            )
            return summary, compacted_history

    def _create_enhanced_agent(self, user_instructions: str):
        """Create agent with user-specific instructions for compaction."""
        try:
            from agents import Agent, function_tool

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

            # Try to create enhanced agent
            try:
                analyze_tool = self._create_instruction_aware_analyze_tool()
                summarize_tool = self._create_instruction_aware_summarize_tool()

                tools = []
                if analyze_tool:
                    tools.append(analyze_tool)
                if summarize_tool:
                    tools.append(summarize_tool)

                return Agent(
                    name="instruction_aware_compactor",
                    instructions=enhanced_system_prompt,
                    model=self.model,
                    tools=tools,
                )
            except Exception as e:
                self.logger.error(f"Error creating enhanced agent: {e}")
                return None

        except ImportError:
            self.logger.warning("Could not import Agent from agents package")
            return None

    def _create_instruction_aware_analyze_tool(self):
        """Create tool that's aware of user instructions."""
        try:
            from agents import function_tool

            def analyze_with_instructions(
                conversation_text: str, user_instructions: str
            ) -> str:
                """
                Analyze conversation with awareness of user instructions.

                Args:
                    conversation_text: Conversation to analyze
                    user_instructions: User's specific instructions

                Returns:
                    Analysis focused on user's priorities
                """
                # Extract keywords from instructions
                instruction_lower = user_instructions.lower()

                # Identify focus topics
                focus_topics = []
                avoid_topics = []

                # Look for "keep", "preserve", "focus on"
                if any(
                    word in instruction_lower
                    for word in ["keep", "preserve", "focus on"]
                ):
                    # Simple extraction of topics after these words
                    for marker in ["keep", "preserve", "focus on"]:
                        if marker in instruction_lower:
                            idx = instruction_lower.find(marker)
                            after = instruction_lower[idx + len(marker) :].strip()
                            topic = after.split(",")[0].split(" ")[0]
                            if topic:
                                focus_topics.append(topic)

                # Look for "remove", "avoid", "exclude"
                if any(
                    word in instruction_lower for word in ["remove", "avoid", "exclude"]
                ):
                    for marker in ["remove", "avoid", "exclude"]:
                        if marker in instruction_lower:
                            idx = instruction_lower.find(marker)
                            after = instruction_lower[idx + len(marker) :].strip()
                            topic = after.split(",")[0].split(" ")[0]
                            if topic:
                                avoid_topics.append(topic)

                analysis = [f"Analysis based on user instructions:"]
                if focus_topics:
                    analysis.append(f"Focus topics: {', '.join(focus_topics)}")
                if avoid_topics:
                    analysis.append(f"Avoid topics: {', '.join(avoid_topics)}")

                return "\n".join(analysis)

            return function_tool(analyze_with_instructions)
        except ImportError:
            self.logger.debug("Could not create instruction aware analyze tool")
            return None
        except Exception as e:
            self.logger.error(f"Error creating instruction aware analyze tool: {e}")
            return None

    def _create_instruction_aware_summarize_tool(self):
        """Create summarization tool that respects user instructions."""
        try:
            from agents import function_tool

            def summarize_with_instructions(content: str, instructions: str) -> str:
                """
                Summarize content following specific user instructions.

                Args:
                    content: Content to summarize
                    instructions: User instructions for what to preserve/remove

                Returns:
                    Summary following user instructions
                """
                # Simple implementation - in production would be more sophisticated
                lines = content.split("\n")
                instruction_lower = instructions.lower()

                # Filter lines based on instructions
                filtered_lines = []

                for line in lines:
                    line_lower = line.lower()
                    should_include = True

                    # Check if line contains topics to avoid
                    avoid_words = ["remove", "avoid", "exclude"]
                    for word in avoid_words:
                        if word in instruction_lower:
                            # Extract topic to avoid (simplified)
                            topic_idx = instruction_lower.find(word)
                            if topic_idx >= 0:
                                after = instruction_lower[
                                    topic_idx + len(word) :
                                ].strip()
                                topic = after.split()[0] if after.split() else ""
                                if topic and topic in line_lower:
                                    should_include = False
                                    break

                    if should_include:
                        filtered_lines.append(line)

                return "\n".join(filtered_lines)

            return function_tool(summarize_with_instructions)
        except ImportError:
            self.logger.debug("Could not create instruction aware summarize tool")
            return None
        except Exception as e:
            self.logger.error(f"Error creating instruction aware summarize tool: {e}")
            return None

    def _format_conversation(self, conversation_history: List[Dict]) -> str:
        """Format conversation history for agent processing."""
        if not conversation_history:
            return "No conversation history available."

        formatted = []
        for msg in conversation_history:
            if isinstance(msg, dict):
                role = msg.get("role", "unknown")
                content = msg.get("content", "")
                formatted.append(f"{role.upper()}: {content}")
            else:
                formatted.append(str(msg))

        return "\n\n".join(formatted)

    def _summary_to_conversation_history(
        self, summary: str, original_history: List[Dict]
    ) -> List[Dict]:
        """Convert agent summary back into conversation history format.

        Creates a compacted conversation by treating the summary as a single assistant message
        that represents the compacted context, preserving the original conversation structure
        but with reduced content.
        """
        try:
            # Create a single compacted message from the summary
            compacted_message = {"role": "assistant", "content": summary}

            # Return just the compacted summary as the new conversation
            # This effectively replaces all previous turns with a single summary turn
            return [compacted_message]

        except Exception as e:
            self.logger.error(f"Error converting summary to conversation history: {e}")
            # Fallback: return the original history
            return original_history

    def infer_token_target(self, instructions: str, current_tokens: int) -> int:
        """Intelligently infer target token count from natural language instructions.

        Examples:
        - "compact aggressively" -> 30% of current tokens
        - "reduce significantly" -> 50% of current tokens
        - "light compaction" -> 85% of current tokens
        - "around 2000 tokens" -> 2000 tokens
        """
        instructions_lower = instructions.lower()

        # Aggressive compaction indicators
        if any(
            word in instructions_lower
            for word in [
                "aggressive",
                "heavily",
                "drastically",
                "severely",
                "major",
                "significant",
            ]
        ):
            return int(current_tokens * 0.3)

        # Moderate compaction indicators
        elif any(
            word in instructions_lower
            for word in [
                "moderately",
                "somewhat",
                "a bit",
                "significantly",
                "substantially",
            ]
        ):
            return int(current_tokens * 0.5)

        # Light compaction indicators
        elif any(
            word in instructions_lower
            for word in ["light", "lightly", "minimal", "small", "slightly", "gently"]
        ):
            return int(current_tokens * 0.85)

        # Look for specific number patterns
        token_patterns = [
            r"(\d+)\s*tokens?",
            r"(\d+)\s*token",
            r"around\s*(\d+)",
            r"about\s*(\d+)",
            r"~(\d+)",
            r"approximately\s*(\d+)",
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


class ContextAnalyzer:
    """
    Analyzes conversation context to identify:
    - Active tasks and ongoing work
    - Key decisions and conclusions
    - Important file references and changes
    - Critical user preferences and constraints
    - Tool usage patterns and results
    """

    def __init__(self, logger: logging.Logger):
        self.logger = logger

    def extract_key_elements(self, conversation: List[Dict]) -> Dict[str, Any]:
        """Extract structured key elements from conversation."""
        elements = {
            "active_tasks": [],
            "file_references": [],
            "decisions": [],
            "tools_used": [],
            "action_items": [],
        }

        # Convert conversation to text for analysis
        conversation_text = self._conversation_to_text(conversation)

        # Extract active tasks
        task_indicators = [
            "todo:",
            "fix:",
            "implement",
            "add",
            "create",
            "update",
            "modify",
        ]
        for line in conversation_text.split("\n"):
            line_lower = line.lower()
            if any(indicator in line_lower for indicator in task_indicators):
                elements["active_tasks"].append(line.strip())

        # Extract file references
        import re

        file_pattern = r"@[\w\-./]+\.[\w]+"
        elements["file_references"] = re.findall(file_pattern, conversation_text)

        # Extract decisions
        decision_indicators = ["decided", "agreed", "conclusion", "final", "settled on"]
        for line in conversation_text.split("\n"):
            line_lower = line.lower()
            if any(indicator in line_lower for indicator in decision_indicators):
                elements["decisions"].append(line.strip())

        # Extract action items
        action_indicators = ["next step", "will do", "need to", "should", "plan to"]
        for line in conversation_text.split("\n"):
            line_lower = line.lower()
            if any(indicator in line_lower for indicator in action_indicators):
                elements["action_items"].append(line.strip())

        return elements

    def calculate_importance_score(self, element: str) -> float:
        """Score elements by importance for retention."""
        # Simple scoring system - can be made more sophisticated
        score = 0.5  # Base score

        # Higher score for recent content (assume later in conversation)
        # This is simplified - in real implementation would use timestamps

        # Higher score for file references
        if "@" in element and any(
            ext in element for ext in [".py", ".js", ".lua", ".md"]
        ):
            score += 0.3

        # Higher score for action items
        if any(
            word in element.lower() for word in ["implement", "fix", "add", "create"]
        ):
            score += 0.2

        # Higher score for decisions
        if any(word in element.lower() for word in ["decided", "agreed", "conclusion"]):
            score += 0.2

        return min(1.0, score)

    def _conversation_to_text(self, conversation: List[Dict]) -> str:
        """Convert conversation list to text for analysis."""
        lines = []
        for msg in conversation:
            if isinstance(msg, dict):
                role = msg.get("role", "")
                content = msg.get("content", "")
                lines.append(f"{role}: {content}")
            else:
                lines.append(str(msg))
        return "\n".join(lines)
