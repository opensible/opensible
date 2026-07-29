# Security Policy

The OpenSible team and community take security issues seriously. Thank you for helping to responsibly disclose vulnerabilities and keep our users safe.

## Supported versions

We patch security issues on the latest minor release. Older minors receive fixes only for critical vulnerabilities on a best-effort basis.

| Version | Supported          |
|---------|--------------------|
| 0.1.x   | :white_check_mark: |
| < 0.1   | :x:                |

## Reporting a vulnerability

**Please do not report security vulnerabilities through public GitHub issues, discussions, or pull requests.**

Instead, report them privately using one of the following channels:

- **Email**: `security@opensible.com` (preferred). PGP key available on request.
- **GitHub Security Advisories**: [Report a vulnerability](https://github.com/opensible/opensible/security/advisories/new) directly on the repository.

Include as much of the following as possible:

- A clear description of the vulnerability and its impact
- Steps to reproduce (proof-of-concept, scripts, or a minimal repository)
- Affected component (backend, worker-go, frontend, blueprint) and version
- Any suggested remediation
- Whether you plan to disclose publicly and your preferred timeline

## Our commitment

- We will acknowledge receipt of your report within **5 business days**.
- We will provide an initial assessment (severity, scope, plan) within **10 business days**.
- We will keep you informed of progress and coordinate a disclosure date with you.
- We will credit reporters in the release notes and security advisory unless anonymity is requested.

## Coordinated disclosure

We follow a 90-day coordinated disclosure window by default. If a fix is not feasible within that window we will communicate a revised timeline. Please give us a reasonable opportunity to remediate before making the issue public.

## Scope

In scope:

- OpenSible backend (`ossopensible/opensible-server`)
- OpenSible worker (`ossopensible/opensible-worker`, `worker-go/`)
- OpenSible web console (`ossopensible/opensible-console`, `src/`)
- Official Docker images published under `ossopensible/*`
- IaC blueprints shipped in this repository (`IaC/`)

Out of scope:

- Vulnerabilities in third-party dependencies (please report upstream)
- Self-inflicted misconfiguration of a self-hosted deployment
- Denial-of-service via unrealistic traffic volumes
- Social engineering of maintainers or users

## Hardening guidance

Operators should follow the [Security hardening guide]([./07-operations/01-security-hardening.md](https://docs.opensible.com/opensiblev1/installation-with-docker)) and rotate the required cryptographic secrets (`JWT_SECRET_KEY`, `INTERNAL_CALL_SECRET`, `GLOBAL_SECRETS_ENCRYPTION_KEY`) periodically.

Thank you for helping keep OpenSible and its users safe.
