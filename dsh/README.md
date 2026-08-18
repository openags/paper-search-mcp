# paper-search-mcp-dsh

DeepSeek Harness ([dsh](https://github.com/deepseek-ai/deepseek-harness)) profile bundle for [paper-search-mcp](https://github.com/openags/paper-search-mcp): boots the published MCP server and registers its tools in any dsh profile as `mcp__paper-search__*`.

The bundle only mounts a composition row — it never patches or replaces the MCP server itself, and its tool namespace is isolated from anything else in the profile.

## Install

**Prerequisites**: [uv](https://docs.astral.sh/uv/getting-started/installation/) (the default launcher is `uvx`) and [pnpm](https://pnpm.io/installation) (`dsh plugin` forwards to pnpm). Commands below use the `npx @deepseek-ai/dsh` launcher (no global install); with a global dsh install, drop the prefix.

Clone the paper-search-mcp repository and link the bundle into your profile:

```sh
git clone https://github.com/openags/paper-search-mcp.git
cd paper-search-mcp
npx @deepseek-ai/dsh plugin --profile web add link:./dsh
```

The `link:` spec symlinks the live checkout into the profile, so the profile always reads this directory's `cordis.patch.yml` — `git pull` in the checkout is the upgrade path.

Replace `web` with your profile name — any profile works; one that does not exist yet is initialized automatically. Restart the profile (`npx @deepseek-ai/dsh web`, or relaunch) to activate. The model then sees `mcp__paper-search__search_papers`, `mcp__paper-search__download_with_fallback`, `mcp__paper-search__search_arxiv`, and the other server tools.

Remove: `npx @deepseek-ai/dsh plugin --profile web remove paper-search-mcp-dsh` (the row is reconciled out of the composition automatically).

## Optional API keys

The server loads `~/.config/paper-search-mcp/.env` itself at startup (see the project's [Environment Variables](https://github.com/openags/paper-search-mcp#environment-variables-env-file) section) — create that file and no dsh-side configuration is needed.

DSH deliberately scrubs credential-shaped ambient env vars (and all `DSH_*` vars) from spawned children, so shell exports do **not** reach the server. To forward variables explicitly, override the `mcp-paper-search` row in `~/.dsh/profiles/<name>/cordis.patch.yml`. A config override replaces the entire object, so restate every field you want to keep:

```yaml
- id: mcp-paper-search
  config:
    serverName: paper-search
    transport: stdio
    command: uvx
    args: ['paper-search-mcp']
    toolCallTimeoutMs: 120000
    env:
      PAPER_SEARCH_MCP_UNPAYWALL_EMAIL: !!js process.env.PAPER_SEARCH_MCP_UNPAYWALL_EMAIL
      PAPER_SEARCH_MCP_SEMANTIC_SCHOLAR_API_KEY: !!js process.env.PAPER_SEARCH_MCP_SEMANTIC_SCHOLAR_API_KEY
```

## Alternate launchers

The default `uvx paper-search-mcp` runs the latest PyPI release with no install. The same row override mechanism switches launchers — restate the full config:

| Launcher | `command` | `args` |
|---|---|---|
| `uv` tool run | `uv` | `['tool', 'run', 'paper-search-mcp']` |
| Python module | `python` | `['-m', 'paper_search_mcp.server']` |
| npx via Smithery | `npx` | `['-y', '@smithery/cli', 'run', '@openags/paper-search-mcp']` |

## Optional skill

`skills/paper-search/SKILL.md` (in this repository) is a model-guidance skill (usage workflow, source table, tool mapping) — the MCP tools work without it. From the checkout:

```sh
mkdir -p ~/.dsh/skills && cp -r dsh/skills/paper-search ~/.dsh/skills/
```

## Naming and conflicts

- `serverName: paper-search` namespaces every tool (`mcp__paper-search__*`). It is unique across live `@deepseek-ai/dsh-mcp-client` instances: if you already run paper-search-mcp through your own client row, remove that row or set a distinct `serverName` in one of the two — a duplicate fails the later instance at load.
- The row id `mcp-paper-search` is the stable anchor for user overrides; avoid using it for other rows in the same profile.
- The bundle never touches the Python server or its configuration files; uninstalling the bundle restores the profile exactly.

## Versions

The `package.json` version tracks the PyPI release the bundle was tested with (informational; the test suite asserts the two stay in sync).

## Development

- `package.json` declares `"dsh": { "bundle": { "patch": "./cordis.patch.yml" } }` — that declaration is what makes `dsh plugin` treat the package as a profile layer.
- `cordis.patch.yml` is a loader patch that inserts the `@deepseek-ai/dsh-mcp-client` row; dsh applies bundle patches over the profile's empty root, then the user's `cordis.patch.yml` on top (last write wins per row id).
- Installed via `link:` from a checkout, the profile reads the live patch file, so edits to `cordis.patch.yml` apply on the next profile boot.
