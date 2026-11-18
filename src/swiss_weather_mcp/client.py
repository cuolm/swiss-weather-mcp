"""A test client that lets a local model answer questions with the server's tools, over stdio."""
import argparse
import asyncio
import json
import logging
import sys
from typing import List

from mcp import Client, StdioServerParameters
from mcp.types import TextContent

try:
    from openai import APIConnectionError, APIError, AsyncOpenAI
    from openai.types.chat import (
        ChatCompletionAssistantMessageParam,
        ChatCompletionFunctionToolParam,
        ChatCompletionMessage,
        ChatCompletionMessageFunctionToolCall,
        ChatCompletionMessageParam,
    )
except ModuleNotFoundError as error:
    raise SystemExit(
        "The MCP client needs the OpenAI SDK, which ships in the optional 'client' extra.\n"
        "Install it with one of:\n"
        "    uv tool install 'swiss-weather-mcp[client]'   # installed as a tool\n"
        "    uv sync --extra client                        # from a source checkout\n"
        "    pip install 'swiss-weather-mcp[client]'"
    ) from error

from .log import LOG_LEVELS, setup_logging

logger = logging.getLogger(__name__)

# The server writes its log to the same terminal, so only its warnings and errors are shown, not its
# INFO lines, which would come in between the question and the answer
SERVER_LOG_LEVEL = "WARNING"

# A model that keeps calling tools would otherwise never answer
MAX_TOOL_TURNS = 10


def _parse_args() -> argparse.Namespace:
    """Read the command line options of the test client."""
    parser = argparse.ArgumentParser(description="Run MCP Client")
    parser.add_argument("--model", type=str, required=True,
                        help="Model name as the server reports it")
    parser.add_argument("--base-url", type=str, default="http://localhost:8080/v1",
                        help="OpenAI compatible endpoint of an already running server (default: %(default)s)")
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=LOG_LEVELS,
        help="Logging level for the client (default: INFO)",
    )
    return parser.parse_args()


class ChatLoop:
    """A conversation in which a local model answers questions with the tools of a connected MCP server."""

    def __init__(self, mcp_client: Client, model: str, base_url: str):
        self.mcp_client = mcp_client
        self.llm_client = AsyncOpenAI(base_url=base_url, api_key="not-needed")  # local servers ignore the key
        self.model = model
        self.messages: List[ChatCompletionMessageParam] = []

        # The server sends instructions on how to use its tools when it connects;
        # the MCP types suggest giving them to the model in the system prompt
        instructions = mcp_client.instructions
        if instructions:
            self.messages.append({"role": "system", "content": instructions})

    async def fetch_tool_definitions(self) -> List[ChatCompletionFunctionToolParam]:
        """Fetch the server's tools in the format the OpenAI chat API expects."""
        tools_result = await self.mcp_client.list_tools()
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": tool.input_schema,
                }
            }
            for tool in tools_result.tools
        ]

    async def call_tool(self, tool_name: str, arguments_json: str) -> str:
        """Call a tool with the arguments the model wrote as JSON, and return its answer or error as text."""
        logger.info(f"Tool: {tool_name} called with arguments: {arguments_json}")
        try:
            arguments = json.loads(arguments_json or "{}")  # the model writes these as JSON text
            tool_result = await self.mcp_client.call_tool(tool_name, arguments)
            texts = [content.text for content in tool_result.content if isinstance(content, TextContent)]
            return "\n".join(texts)
        except Exception as error:
            logger.error(f"Tool {tool_name} failed: {error}")
            return f"Error calling tool {tool_name}: {error}"

    def _format_assistant_message(self, model_reply: ChatCompletionMessage) -> ChatCompletionAssistantMessageParam:
        """Keep only what the chat API needs from an assistant message: its text and its tool calls."""
        formatted_message: ChatCompletionAssistantMessageParam = {
            "role": "assistant",
            "content": model_reply.content or ""
        }

        if model_reply.tool_calls:
            formatted_message["tool_calls"] = [
                {
                    "id": tool_call.id,  # each result has to reference the call it answers
                    "type": "function",
                    "function": {
                        "name": tool_call.function.name,
                        "arguments": tool_call.function.arguments,
                    }
                }
                for tool_call in model_reply.tool_calls
                if isinstance(tool_call, ChatCompletionMessageFunctionToolCall)
            ]

        return formatted_message

    async def process_query(self, query: str) -> str:
        """Answer a question: let the model call tools until it gives its final answer, and return it.

        After MAX_TOOL_TURNS rounds of tool calls without a final answer, return a note that it stopped.
        """
        self.messages.append({"role": "user", "content": query})
        tools = await self.fetch_tool_definitions()

        for _ in range(MAX_TOOL_TURNS):
            completion = await self.llm_client.chat.completions.create(
                model=self.model, messages=self.messages, tools=tools
            )
            model_reply = completion.choices[0].message
            assistant_message = self._format_assistant_message(model_reply)
            self.messages.append(assistant_message)

            # A reply without tool calls is the model's final answer
            if not model_reply.tool_calls:
                if model_reply.content is None:  # the model sent no text
                    return ""
                else:
                    return model_reply.content

            for tool_call in model_reply.tool_calls:
                # Only function tools are offered, so no other kind of call comes back
                if not isinstance(tool_call, ChatCompletionMessageFunctionToolCall):
                    continue
                tool_result_text = await self.call_tool(tool_call.function.name, tool_call.function.arguments)
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_result_text,
                })

        return f"[Stopped after {MAX_TOOL_TURNS} rounds of tool calls without a final answer]"


async def _run() -> None:
    """Start the server, then ask the model each question typed in, until /bye."""
    args = _parse_args()
    setup_logging(args.log_level)

    # Launch the server module with the same interpreter running this client
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "swiss_weather_mcp.server", "--log-level", SERVER_LOG_LEVEL],
    )

    # Leaving the block closes the session and stops the server
    async with Client(server_params) as mcp_client:
        # List available tools, names only, the descriptions are multi line docstrings
        tools_result = await mcp_client.list_tools()
        tool_names = ", ".join(tool.name for tool in tools_result.tools)
        logger.info(f"Connected to server with tools: {tool_names}")

        chat = ChatLoop(mcp_client, args.model, args.base_url)
        logger.info("MCP Client started!")
        while True:
            try:
                query = await asyncio.to_thread(input, "\nType your question (or '/bye' to quit): ")
            except EOFError:  # Ctrl+D, or the end of piped input
                query = "/bye"
            query = query.strip()
            if query.lower() == "/bye":
                print("Goodbye!")
                break

            if not query:
                print("Please enter a non-empty query.")
                continue

            logger.info(f"Query: {query}")
            messages_before = len(chat.messages)
            try:
                answer = await chat.process_query(query)
            except APIConnectionError:
                logger.error(f"Could not reach the model server at {args.base_url}. Start it first (see the README), then ask again.")
                del chat.messages[messages_before:]  # the question was not answered, so it is not sent again
                continue
            except APIError as error:
                logger.error(f"The model server returned an error: {error}")
                del chat.messages[messages_before:]
                continue
            print(f"\n{answer}")


def main() -> None:
    """Start the test client from the command line."""
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        logger.info("Interrupted by user, shutting down")


if __name__ == "__main__":
    main()
