"""Claude SDK Client wrapper - maintains session context."""

from pathlib import Path
from typing import Optional, AsyncIterator, Callable

from claude_agent_sdk import (
    ClaudeSDKClient,
    ClaudeAgentOptions,
    AssistantMessage,
    UserMessage,
    TextBlock,
    ResultMessage,
    ToolUseBlock,
    ToolResultBlock,
)


class SkillClient:
    """Wrapper for ClaudeSDKClient that maintains session context.

    Unlike query(), this client keeps context across multiple executions,
    allowing the agent to remember previous steps in the DAG.
    """

    # Default disabled tools (blacklist)
    DEFAULT_DISALLOWED_TOOLS = ["WebSearch", "WebFetch", "AskUserQuestion"]

    def __init__(
        self,
        session_id: str = "orchestrator",
        allowed_tools: Optional[list[str]] = None,
        disallowed_tools: Optional[list[str]] = None,
        cwd: Optional[str] = None,
        log_callback: Optional[Callable[[str, str], None]] = None,
    ):
        self.session_id = session_id
        self.log_callback = log_callback

        # Determine if skills need to be loaded:
        # - When allowed_tools=None, use default value (includes Skill)
        # - When allowed_tools explicitly includes "Skill", need to load
        needs_skills = allowed_tools is None or "Skill" in allowed_tools

        # Merge default blacklist and user-specified blacklist
        final_disallowed = self.DEFAULT_DISALLOWED_TOOLS + (disallowed_tools or [])

        self.options = ClaudeAgentOptions(
            allowed_tools=allowed_tools or [
                "Skill",
                "Bash", "Read", "Write", "Glob", "Grep", "Edit"
            ],
            disallowed_tools=final_disallowed,
            setting_sources=["user", "project"] if needs_skills else None,
            permission_mode="default",
            cwd=cwd or str(Path.cwd()),
            max_buffer_size=10485760,
            model='claude-sonnet-4-6',
        )
        self.client: Optional[ClaudeSDKClient] = None
        self._connected = False

    def _log(self, message: str, level: str = "info") -> None:
        """Internal logging with callback support."""
        if self.log_callback:
            self.log_callback(message, level)

    async def connect(self, initial_prompt: Optional[str] = None) -> None:
        """Initialize the client and optionally send initial prompt."""
        self.client = ClaudeSDKClient(self.options)
        await self.client.connect(initial_prompt)
        self._connected = True

    async def execute(self, prompt: str) -> str:
        """Execute a prompt in the current session, maintaining context.

        Args:
            prompt: The prompt to send to Claude

        Returns:
            The text response from Claude
        """
        if not self._connected or not self.client:
            await self.connect()

        # Log the actual prompt content being sent
        self._log(f"Sending Query:\n{prompt}", "send")

        response_text = ""
        await self.client.query(prompt, session_id=self.session_id)

        async for message in self.client.receive_messages():
            # Handle user messages (contain tool results)
            if isinstance(message, UserMessage):
                if isinstance(message.content, str):
                    # String content is the user's query/prompt
                    self._log(f"User Query: {message.content}", "send")
                else:
                    # List content contains tool results
                    for block in message.content:
                        if isinstance(block, ToolResultBlock):
                            tool_id = getattr(block, 'tool_use_id', 'unknown')
                            self._log(f"Tool Result ({tool_id}): {block.content}", "info")
                        elif isinstance(block, TextBlock):
                            self._log(f"User Text: {block.text}", "info")
                        else:
                            # Other block types
                            block_type = type(block).__name__
                            self._log(f"{block_type}: {block}", "info")

            # Handle assistant messages (contain tool calls and text)
            elif isinstance(message, AssistantMessage):
                if message.error:
                    self._log(f"Assistant ERROR: {message.error}", "error")
                for block in message.content:
                    if isinstance(block, TextBlock):
                        response_text += block.text
                        # Log complete text (visualizer handles multi-line)
                        self._log(block.text, "recv")
                    elif isinstance(block, ToolUseBlock):
                        # Log tool usage with full details
                        self._log(f"Tool: {block.name}", "tool")
                        self._log(f"  Input: {block.input}", "info")

            elif isinstance(message, ResultMessage):
                cost = getattr(message, 'total_cost_usd', 'N/A')
                self._log(f"Execution completed (cost: ${cost})", "ok")
                break  # End signal

            else:
                # Catch unknown message types
                msg_type = type(message).__name__
                self._log(f"Unknown message type: {msg_type} - {message}", "warn")

        return response_text

    async def execute_with_metrics(self, prompt: str) -> tuple[str, dict]:
        """Execute a prompt and return response with metrics.

        Args:
            prompt: The prompt to send to Claude

        Returns:
            Tuple of (response_text, metrics_dict)
            metrics_dict contains: input_tokens, output_tokens, cost_usd
        """
        if not self._connected or not self.client:
            await self.connect()

        self._log(f"Sending Query:\n{prompt}", "send")

        response_text = ""
        metrics = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}

        await self.client.query(prompt, session_id=self.session_id)

        async for message in self.client.receive_messages():
            if isinstance(message, UserMessage):
                if isinstance(message.content, str):
                    self._log(f"User Query: {message.content}", "send")
                else:
                    for block in message.content:
                        if isinstance(block, ToolResultBlock):
                            tool_id = getattr(block, 'tool_use_id', 'unknown')
                            self._log(f"Tool Result ({tool_id}): {block.content}", "info")

            elif isinstance(message, AssistantMessage):
                if message.error:
                    self._log(f"Assistant ERROR: {message.error}", "error")
                for block in message.content:
                    if isinstance(block, TextBlock):
                        response_text += block.text
                        self._log(block.text, "recv")
                    elif isinstance(block, ToolUseBlock):
                        self._log(f"Tool: {block.name}", "tool")
                        self._log(f"  Input: {block.input}", "info")

            elif isinstance(message, ResultMessage):
                metrics["cost_usd"] = getattr(message, 'total_cost_usd', 0.0)
                usage = getattr(message, 'usage', None)
                if usage:
                    metrics["input_tokens"] = usage.get('input_tokens', 0)
                    metrics["output_tokens"] = usage.get('output_tokens', 0)
                self._log(f"Execution completed (cost: ${metrics['cost_usd']:.4f})", "ok")
                break

        return response_text, metrics

    async def stream_execute(self, prompt: str) -> AsyncIterator[str]:
        """Execute a prompt and stream the response.

        Args:
            prompt: The prompt to send to Claude

        Yields:
            Text chunks from Claude's response
        """
        if not self._connected or not self.client:
            await self.connect()

        await self.client.query(prompt, session_id=self.session_id)

        async for message in self.client.receive_messages():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        yield block.text
            elif isinstance(message, ResultMessage):
                break

    async def disconnect(self) -> None:
        """Disconnect the client."""
        if self.client:
            await self.client.disconnect()
            self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.disconnect()
