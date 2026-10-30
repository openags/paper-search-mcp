"""Expose the registered MCP tools without a second implementation or registry.

The tool subcommand and positional/flag convention adapt PR #78 by kalomak.
Discovery and execution use the MCP server's public API, so optional tools,
validation, timeouts, and source restrictions stay with their MCP definitions.
"""

from __future__ import annotations

import argparse
import json
from typing import Any


def _boolean(value: str) -> bool:
    if value.lower() in ("true", "false"):
        return value.lower() == "true"
    raise argparse.ArgumentTypeError("expected true or false")


def _argument_type(schema: dict[str, Any]) -> Any:
    """Convert shell strings; the MCP dispatcher performs final validation."""
    return {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": _boolean,
    }.get(schema.get("type"), json.loads)


def build_tool_parser(tools: list[Any]) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="paper-search tool",
        description="Call the same tools as the MCP server. Required arguments "
        "are positional; optional arguments use --kebab-case flags.",
    )
    parser.add_argument(
        "--list", action="store_true", dest="_list_tools",
        help="List available tools with their MCP schemas and annotations as JSON",
    )
    subparsers = parser.add_subparsers(dest="_tool_name")
    for tool in tools:
        description = tool.description or tool.name
        command = subparsers.add_parser(
            tool.name,
            help=description.splitlines()[0],
            description=description,
            formatter_class=argparse.RawDescriptionHelpFormatter,
            allow_abbrev=False,
        )
        properties = tool.inputSchema.get("properties", {})
        required = set(tool.inputSchema.get("required", []))
        for name, schema in properties.items():
            help_text = schema.get("description", schema.get("title", name))
            if name in required:
                command.add_argument(
                    name, type=_argument_type(schema), help=help_text,
                    **({"choices": schema["enum"]} if "enum" in schema else {}),
                )
                continue
            option = f"--{name.replace('_', '-')}"
            if "default" in schema:
                help_text += f" (default: {schema['default']!r})"
            kwargs: dict[str, Any] = {
                "dest": name, "default": argparse.SUPPRESS, "help": help_text,
            }
            if schema.get("type") == "boolean":
                kwargs["action"] = argparse.BooleanOptionalAction
            else:
                kwargs["type"] = _argument_type(schema)
                if "enum" in schema:
                    kwargs["choices"] = schema["enum"]
            command.add_argument(option, **kwargs)
        command.set_defaults(_tool_parameter_names=tuple(properties))
    return parser


def _print_result(result: Any) -> None:
    """Render the MCP result as ordinary JSON or plain text for shell users."""
    # FastMCP returns (content, structuredContent) for typed return values. Its
    # generated schemas wrap primitive, list, and generic dict returns in result.
    if isinstance(result, tuple):
        _content, structured = result
        result = structured.get("result", structured)
        if isinstance(result, str):
            print(result)
        else:
            print(json.dumps(result, indent=2, default=str))
    elif isinstance(result, dict):
        print(json.dumps(result, indent=2, default=str))
    else:
        # Tools without a structured output schema return MCP content blocks.
        # All current tools return text; retain other blocks in JSON if added.
        if all(getattr(item, "type", None) == "text" for item in result):
            print("\n".join(item.text for item in result))
        else:
            print(json.dumps([item.model_dump(mode="json") for item in result], indent=2))


async def cmd_tool(args: argparse.Namespace) -> int:
    # Do not import server (or instantiate all its connectors) for legacy CLI
    # commands, top-level help, or parser construction.
    try:
        from .server import mcp

        tools = await mcp.list_tools()
        parser = build_tool_parser(tools)
        argv = list(args.tool_args)
        if args.tool_help:
            argv.insert(0, "--help")
        if args.list_tools:
            argv.insert(0, "--list")
        parsed = parser.parse_args(argv)
        if parsed._list_tools:
            if parsed._tool_name is not None:
                parser.error("--list cannot be combined with a tool invocation")
            print(json.dumps(
                {"tools": [tool.model_dump(mode="json", exclude_none=True) for tool in tools]},
                indent=2,
            ))
            return 0
        if parsed._tool_name is None:
            parser.error("a tool name or --list is required")
        arguments = {
            name: getattr(parsed, name)
            for name in parsed._tool_parameter_names
            if hasattr(parsed, name)
        }
        _print_result(await mcp.call_tool(parsed._tool_name, arguments))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "error", "message": str(exc)}))
        return 1
