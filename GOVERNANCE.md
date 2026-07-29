# Governance

OpenSible is an open-source project stewarded by a small core team with contributions from a growing community. This document describes how decisions are made and how contributors can grow into maintainer roles.

## Roles

### Users
Anyone who uses OpenSible. Users are encouraged to file issues, ask questions in Discussions, and help other users.

### Contributors
Anyone who submits a pull request, issue, or documentation improvement that is accepted into the project. Contributors are recognized in release notes and the `CONTRIBUTORS` file.

### Maintainers
Contributors with commit access to the repository. Maintainers:

- Review and merge pull requests
- Triage issues and manage labels
- Cut releases and publish artifacts
- Enforce the [Code of Conduct](./CODE_OF_CONDUCT.md)

Maintainers are added by consensus of the existing maintainer team, typically after a sustained record of high-quality contributions (usually 3+ merged non-trivial PRs and demonstrated ability to review others' work).

### Core team
A small group of maintainers responsible for the overall technical direction, security response, release cadence, and community health. The core team resolves disputes when consensus among maintainers cannot be reached.

## Decision making

We prefer **lazy consensus**: proposals move forward if no maintainer objects within a reasonable review window (typically 3 business days for non-trivial changes).

- **Small changes** (bug fixes, docs, refactors): one maintainer approval is sufficient.
- **Non-trivial features**: two maintainer approvals; open a discussion or design issue first.
- **Breaking changes / architectural shifts**: require core team consensus and a documented migration path.
- **Security fixes**: fast-tracked through the private security process (see [`SECURITY.md`](./SECURITY.md)).

## Release process

- Releases follow [Semantic Versioning](https://semver.org/).
- Release notes live under [`release-notes/`](./release-notes/).
- Docker images are published to Docker Hub under `ossopensible/*`.

## Becoming a maintainer

There is no formal application. If you'd like to become a maintainer:

1. Contribute consistently — code, reviews, documentation, or issue triage all count.
2. Demonstrate good judgment in reviews and community interactions.
3. An existing maintainer will nominate you when the time is right.

## Removing maintainers

Maintainers who are inactive for 12+ months, or who violate the Code of Conduct, may be moved to emeritus status. This is not punitive — we simply want the active roster to reflect who is actually stewarding the project.

## Amending this document

Changes to governance are proposed via pull request and require core team consensus.
