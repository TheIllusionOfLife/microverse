# Security Policy

## Supported Versions

Security fixes target the current `main` branch and the latest tagged release.
This project is pre-1.0, so older tags are not guaranteed to receive backported
fixes.

## Reporting a Vulnerability

Do not open a public issue for a suspected vulnerability. Report it privately to
the repository owner through GitHub's private vulnerability reporting flow if it
is available, or by contacting the maintainer listed in `pyproject.toml`.

Please include:

- affected commit or release;
- steps to reproduce;
- impact and any local data involved;
- suggested mitigation, if known.

## Local Data And Generated Content

Microverse is designed for local Ollama execution, but it writes generated and
operator-controlled content to `data/` and `harvest/`. Treat those directories as
potentially sensitive and untrusted:

- do not commit runtime databases, harvested artifacts, logs, or snapshots;
- review generated artifacts before sharing them;
- do not run generated code or shell snippets without inspection;
- avoid putting secrets in prompts, lore, artifacts, metrics, or logs.

## Dependency And CI Security

Dependencies are locked with `uv.lock`. Pull requests should keep the lockfile
in sync with `pyproject.toml`, pass dependency audit checks, and avoid adding
network services or credential handling unless the threat model is updated.
