# CLI tool parity provenance

The `paper-search tool` command resolves
[issue #77](https://github.com/openags/paper-search-mcp/issues/77) and adapts the
CLI subcommand, positional/flag convention, and usage examples introduced by
[Kallemakela (`@kalomak`) in PR #78](https://github.com/openags/paper-search-mcp/pull/78).
Its original head is
[`d9795de4f8043c2b3d1f4e64ad97d6779e0b999d`](https://github.com/openags/paper-search-mcp/commit/d9795de4f8043c2b3d1f4e64ad97d6779e0b999d).
The source commits associate this author with `kalle.makela@aalto.fi`.

This integration derives the CLI from the MCP server's public `list_tools` and
`call_tool` APIs instead of maintaining another tool list or moving `server.py`
to `api.py`. It keeps existing module and console entrypoints, transport setup,
annotations, argument validation, source restrictions, timeout behavior, and
stdio process cleanup in their existing implementations. Ordinary CLI commands
remain lazy and retain their existing behavior. Boolean options are explicit
positive/negative flags rather than toggles with inverted meanings.

The integration commit must preserve the original PR head as an additional
parent and include this trailer:

```text
Co-authored-by: Kallemakela <kalle.makela@aalto.fi>
```

`tests/test_cli_tools.py` provides deterministic discovery, argument, execution,
error, optional-source, timeout, stdout, and entrypoint coverage. Provider
requests are mocked or avoided; it is not a live-network validation of any
academic platform.
