from __future__ import annotations

import argparse
import inspect
import json
from typing import Any, get_args, get_origin

from .api import TOOLS


def _argument_type(annotation: Any) -> Any:
    if annotation is inspect._empty:
        return str
    origin = get_origin(annotation)
    if origin is None:
        return bool if annotation is bool else annotation
    args = [arg for arg in get_args(annotation) if arg is not type(None)]
    if len(args) == 1 and args[0] in (str, int, float, bool):
        return args[0]
    return str


def _add_tool_argument(parser: argparse.ArgumentParser, name: str, parameter: inspect.Parameter) -> None:
    arg_type = _argument_type(parameter.annotation)
    if parameter.default is inspect._empty:
        parser.add_argument(name, type=arg_type)
        return
    option = f"--{name.replace('_', '-')}"
    if arg_type is bool:
        action = "store_false" if parameter.default else "store_true"
        parser.add_argument(option, action=action, default=parameter.default)
        return
    parser.add_argument(option, type=arg_type, default=parameter.default)


def _add_tool_command(subparsers: Any, func: Any) -> None:
    doc = inspect.getdoc(func) or ""
    parser = subparsers.add_parser(
        func.__name__,
        help=doc.splitlines()[0] if doc else func.__name__,
        description=doc,
    )
    for name, parameter in inspect.signature(func).parameters.items():
        _add_tool_argument(parser, name, parameter)
    parser.set_defaults(tool_handler=func)


def add_tool_commands(tool_parser: argparse.ArgumentParser) -> None:
    tool_subparsers = tool_parser.add_subparsers(dest="tool_name", required=True)
    for tool in TOOLS:
        _add_tool_command(tool_subparsers, tool)


async def cmd_tool(args: argparse.Namespace) -> int:
    kwargs = {
        key: value
        for key, value in vars(args).items()
        if key not in {"command", "tool_name", "tool_handler"}
    }
    try:
        result = await args.tool_handler(**kwargs)
        if isinstance(result, str):
            print(result)
        else:
            print(json.dumps(result, indent=2, default=str))
        return 0
    except Exception as e:
        print(json.dumps({"status": "error", "message": str(e)}))
        return 1
