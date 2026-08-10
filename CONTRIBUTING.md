# Contributing

Help us build the future of unified infrastructure and configuration management.

### Our contribution philosophy
OpenSible is built by and for the community. We believe that open collaboration leads to better software. Whether you are a seasoned developer, a documentation specialist, or a first-time contributor, there is a place for you in the OpenSible project. We value all forms of contribution, from deep core code changes to reporting a simple typo.

### How to contribute
We follow the standard GitHub fork-and-pull-request model for all contributions.

1. **Create or discuss an issue**: Before starting any implementation, create an issue describing the proposed change, feature, or improvement. Discuss the potential implementation and approach with the maintainers and wait for confirmation before starting work.
2. **Find an issue**: Look for issues labeled "good first issue" or "help wanted" on our GitHub tracker.
3. **Fork the repo**: Create a fork of the OpenSible repository in your own GitHub account.
4. **Branching**: Create a new branch for your work (e.g., `feature/new-aws-stack` or `fix/worker-log-caching`).
5. **Develop**: Make your changes. If you are adding code, please ensure you also add relevant tests.
6. **Lint and Test**: Run the CI suites locally before opening a PR:

   ```bash
   # Server
   cd server && pip install -r requirements-dev.txt && pytest

   # Worker
   cd worker && go test ./...

   # Console
   cd console && bun install && bun run build
   ```
7. **Pull Request**: Open a PR against our `develop` branch. Provide a clear description of what your PR does and why it is needed.
8. **Code Review**: A maintainer will review your PR. Be prepared to make adjustments based on their feedback.

### Developer resources
*   **Local setup**: See the [Developer Guide](../) for detailed instructions on setting up your environment.
*   **Architecture**: Familiarize yourself with our [Architecture documentation](../) to understand how the components interact.
*   **Coding standards**: We use TypeScript for the backend and frontend, and Go for the workers. Please follow the established patterns in the codebase.

### Documentation contributions
Documentation is a first-class citizen in OpenSible. If you find a section that is confusing or out of date:
*   Edit the `.md` files directly in your fork.
*   Ensure you follow the sentence-case heading style.
*   Submit a PR with the "documentation" label.

### Adding blueprints and providers
One of the best ways to contribute is by adding new stack blueprints or expanding our cloud provider support.
*   If you've created a reusable blueprint for a common architecture (e.g., a three-tier web app on Azure), please share it!
*   Refer to the "Adding a Cloud Provider" guide in the developer section for technical details on extending our provider layer.

### Recognition and community
All contributors are recognized in our release notes and in our `CONTRIBUTORS` file. We are always looking for regular contributors to join our maintainer team.

See also: [Community and support](3-community-and-support), [Code of conduct](5-code-of-conduct)

### Community recognition
We believe in acknowledging the hard work of our community. Major contributors are featured on our website and may be invited to speak at our community events. We also offer "OpenSible Contributor" badges that you can display on your GitHub profile to showcase your involvement in the project.

### Mentorship program
If you are new to open source and want to contribute but don't know where to start, feel free to reach out to one of the maintainers community@opensible.com. We are happy to provide mentorship and guidance to help you make your first successful pull request to the OpenSible project.
