# Issue tracker: GitHub

Issues and specs live in GitHub Issues for
Kmsbismarckx/test-finances. Use the gh CLI.

## Conventions

- Verify the target with `gh repo view --json nameWithOwner`
  before creating the first issue of a session.
- Create: `gh issue create --title "..." --body-file <file>`.
- Read: `gh issue view <number> --json number,title,body,labels,comments`.
- List: `gh issue list --state open`, with appropriate label filters.
- Comment: `gh issue comment <number> --body-file <file>`.
- Labels: `gh issue edit <number> --add-label "..."` or
  `--remove-label "..."`. See triage-labels.md.
- Close: post an explanation as a comment, then run
  `gh issue close <number>`.
- Link child issues using native GitHub sub-issues where supported.
  Otherwise add `Part of #<parent>` to the child and link it
  from a task list in the parent.
- Record blockers using native GitHub issue dependencies where
  supported. Otherwise add `Blocked by: #<n>, #<n>` to the body.
  An issue is unblocked when all blockers are closed.

File issues only in the user's repository.

## Pull requests as a triage surface

PRs as a request surface: no.

## Skill operations

When asked to publish to the issue tracker, create a GitHub issue.
When asked to fetch a ticket, read the referenced GitHub issue.

## Wayfinding operations

- Map: one issue labelled `wayfinder:map`, containing
  Notes / Decisions-so-far / Fog.
- Children: sub-issues labelled `wayfinder:<type>`, where type is
  research, prototype, grilling, or task.
- Frontier: open children with no open blockers or assignee;
  choose the first in map order.
- Claim: assign the issue to the driving developer before work.
- Resolve: comment with the answer, close the child, and append
  a summary and link to the map's Decisions-so-far.
