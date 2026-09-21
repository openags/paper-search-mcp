# Contributing to paper-search-mcp

Thank you for contributing code, tests, documentation, bug reports, or design
feedback. Open focused pull requests where possible and describe how the change
was tested.

## Pull request checklist

- Keep the change scoped to one problem or one cohesive group of problems.
- Add deterministic tests for behavior changes.
- Call out live-network validation separately from mocked tests.
- List any earlier issue or pull request whose code, tests, or design was used.
- Keep `Co-authored-by` trailers at the end of the commit message when the work
  substantially incorporates another contributor's code or tests.

## Maintainer attribution policy

Maintainers should preserve contributor credit as part of the merge, not only
in a closing comment.

1. Prefer updating and merging the contributor's original pull request. Ask for
   a rebase or use maintainer edits when practical.
2. If several pull requests must be consolidated, list every source pull
   request in the integration pull request under **Attribution**.
3. When code or tests are copied, adapted, or materially reimplemented from a
   source pull request, add its human author to the integration **commit
   message** with an email associated with their GitHub account:

   ```text
   Co-authored-by: NAME <EMAIL>
   ```

4. Credit issue reports, review findings, and general inspiration with links and
   `Thanks-to` text. Do not use a co-author trailer when no authored code or
   tests were incorporated.
5. Do not close a source pull request as superseded until the replacement pull
   request contains the provenance links and its final squash message has been
   checked for the required trailers.
6. When merging through the API, do not replace a trailer-bearing commit
   message with a custom message that omits the trailers.

The pull request's Attribution section is the reviewable provenance record. A
trailer written only in the pull request description does not grant commit
credit; it must also be present in the integration commit and final squash
message.

Before deleting the integration branch, verify the result on the default branch:

```bash
git fetch origin main
git show -s --format=full origin/main
```

For prior consolidated changes, see
[Contribution provenance](docs/CONTRIBUTION_PROVENANCE.md).
