# paper-search-mcp-dsh

DeepSeek Harness ([dsh](https://github.com/deepseek-ai/deepseek-harness)) profile bundle for [paper-search-mcp](https://github.com/openags/paper-search-mcp): boots the published MCP server and registers its tools in any dsh profile as `mcp__paper-search__*`.

The bundle only mounts a composition row — it never patches or replaces the MCP server itself, and its tool namespace is isolated from anything else in the profile.

## Install

**Prerequisites**: [uv](https://docs.astral.sh/uv/getting-started/installation/) (the default launcher is `uvx`) and [pnpm](https://pnpm.io/installation) (`dsh plugin` forwards to pnpm). Commands below use the `npx @deepseek-ai/dsh` launcher (no global install); with a global dsh install, drop the prefix.

Clone the paper-search-mcp repository and link the bundle into your profile:

```sh
git clone https://github.com/openags/paper-search-mcp.git
cd paper-search-mcp
npx @deepseek-ai/dsh@0.2.0-rc.2 plugin --profile web add link:./dsh
```

The `link:` spec symlinks the live checkout into the profile, so the profile always reads this directory's `cordis.patch.yml` — `git pull` updates the bundle configuration only. The default launcher runs the separately published Python package; it does not run this checkout or guarantee an update of the cached package.

Replace `web` with your profile name — any profile works; one that does not exist yet is initialized automatically. Restart the profile (`npx @deepseek-ai/dsh@0.2.0-rc.2 web`, or relaunch) to activate. The model then sees `mcp__paper-search__search_papers`, `mcp__paper-search__download_with_fallback`, `mcp__paper-search__search_arxiv`, and the other server tools.

Remove: `npx @deepseek-ai/dsh@0.2.0-rc.2 plugin --profile web remove paper-search-mcp-dsh` (the row is reconciled out of the composition automatically).

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

The default `uvx paper-search-mcp` runs the PyPI package in a managed tool environment, which may be cached. Use an explicit package version for reproducibility or the source-checkout launcher below for unreleased fixes. The same row override mechanism switches launchers — restate the full config:

| Launcher | `command` | `args` |
|---|---|---|
| `uv` tool run | `uv` | `['tool', 'run', 'paper-search-mcp']` |
| Source checkout | `uv` | `['run', '--directory', '/absolute/path/to/paper-search-mcp', 'paper-search-mcp']` |
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
- The bundle never touches the Python server or its configuration files; uninstalling removes its composition row, but does not delete downloaded papers, package caches, or user-managed settings.

## Versions

The commands and MCP-client dependency pin DSH `0.2.0-rc.2` to avoid mixing preview generations. Validation uses Node.js 24; Node.js 22.12 or newer is required by the current CLI dependency stack. Update the pin only after revalidating installation and MCP discovery.

The `package.json` version tracks this repository's Python project version. The local smoke test uses the checked-out server; it does not certify that an older PyPI release includes unreleased repository changes.

## Development

- `package.json` declares `"dsh": { "bundle": { "patch": "./cordis.patch.yml" } }` — that declaration is what makes `dsh plugin` treat the package as a profile layer.
- `cordis.patch.yml` is a loader patch that inserts the `@deepseek-ai/dsh-mcp-client` row; dsh applies bundle patches over the profile's empty root, then the user's `cordis.patch.yml` on top (last write wins per row id).
- Installed via `link:` from a checkout, the profile reads the live patch file, so edits to `cordis.patch.yml` apply on the next profile boot.

## Validation boundary

The offline contract tests validate this repository bundle and server. A DSH profile installation, boot, and tool-registry smoke test are exercised separately against the pinned DSH version. Model-mediated calls require separately configured model access and are not part of this local smoke test. DSH is in developer preview and can introduce breaking changes. No model credentials are bundled.
