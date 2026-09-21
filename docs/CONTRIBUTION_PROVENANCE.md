# Contribution provenance

This ledger records source pull requests that were consolidated into a later
maintainer pull request. It supplements Git history; it is not intended to be a
complete list of repository contributors.

## Consolidation PR #115

| Source PR | Contributor | Incorporated contribution |
| --- | --- | --- |
| [#50](https://github.com/openags/paper-search-mcp/pull/50) | `@Copilot` (bot) | OAuth compatibility documentation used in the consolidated documentation update. |
| [#54](https://github.com/openags/paper-search-mcp/pull/54) | `@Copilot` (bot) | Google Scholar consent-interstitial detection and retry behavior. |
| [#62](https://github.com/openags/paper-search-mcp/pull/62) | `@dli1986` (PR) and `@DuoLi723` (commit) | Diagnosis and handling of non-datetime publication dates from Zenodo and HAL. |
| [#84](https://github.com/openags/paper-search-mcp/pull/84) | `@berntson` | Resolving relative Sci-Hub PDF paths against the redirected response host. |
| [#104](https://github.com/openags/paper-search-mcp/pull/104) | `@mayuriphad` | Tolerant `Paper.to_dict()` date serialization. |
| [#105](https://github.com/openags/paper-search-mcp/pull/105) | `@Aleteria031` | Reporting invalid sources in mixed unified searches. |
| [#110](https://github.com/openags/paper-search-mcp/pull/110) | `@theomgdev` | Preserving Zenodo and HAL author names as lists. |
| [#111](https://github.com/openags/paper-search-mcp/pull/111) | `@rokokol` | Surfacing Semantic Scholar API failures instead of reporting false zero results. |
| [#112](https://github.com/openags/paper-search-mcp/pull/112) | `@feiiiiii5` | Parser regression coverage for Zenodo and HAL author lists. |
| [#113](https://github.com/openags/paper-search-mcp/pull/113) | `@feiiiiii5` | HTTPS arXiv requests and phrase quoting for plain multi-word queries. |

The human contributors above are included as co-authors of the follow-up
attribution commit so their GitHub contribution credit is present on the
default branch without rewriting published history.

Their original PR commits are also connected to the default-branch history by
a tree-preserving provenance merge. The merge keeps the already reviewed code
tree unchanged while making the real, original commit authors reachable from
the default branch for repository contributor statistics.

## Consolidation PR #116

| Source PR | Contributor | Incorporated contribution |
| --- | --- | --- |
| [#49](https://github.com/openags/paper-search-mcp/pull/49) | `@Copilot` (bot) | Non-nullable MCP input schema compatibility. |
| [#51](https://github.com/openags/paper-search-mcp/pull/51) | `@Copilot` (bot) | Bounded Google Scholar tool execution. |
| [#53](https://github.com/openags/paper-search-mcp/pull/53) | `@Copilot` (bot) | Documented bioRxiv DOI, interval, category, and recent-paper query modes. |
| [#55](https://github.com/openags/paper-search-mcp/pull/55) | `@Copilot` (bot) | Per-source timeout isolation with partial unified-search results. |

These source pull requests were bot-authored, so no missing human contributor
credit needed to be repaired for PR #116.
