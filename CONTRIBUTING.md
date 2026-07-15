# Contributing

Thank you for helping improve Midom Local Event Video Processor.

This project is intentionally small and operationally conservative. It exists to let trusted Midom project members run local deterministic Event Video Processing jobs for Midom Video Collection Events.

## Development Setup

Use Python 3.10 or newer.

```bash
python3.10 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[release]'
```

Run tests:

```bash
PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 python -m pytest -q tests -p no:cacheprovider
```

Compile check:

```bash
PYTHONPYCACHEPREFIX=/tmp/midom-local-processor-pycache python -m compileall midom_local_processor scripts
```

## Design Rules

- Treat the local processor as untrusted compute from Midom's point of view.
- Do not add publication, moderation, approval, or broad project-file access to the worker.
- Keep worker inputs claimed-job scoped.
- Do not accept arbitrary FFmpeg command lines from Midom.
- Prefer explicit fixed processing profiles over server-supplied command fragments.
- Keep Windows and Linux release paths first-class. macOS packaging is intentionally out of scope for the first release.

## Pull Request Expectations

Before opening a pull request:

- Run the test suite.
- Run the compile check.
- Confirm no local credentials, pairing codes, release caches, generated bundles, virtual environments, or raw FFmpeg binaries are included.
- Update `README.md`, `CHANGELOG.md`, or `docs/release-process.md` when behavior or release steps change.

## Security Changes

Security-sensitive changes should be discussed privately first when they involve token handling, input download authorization, artifact validation, command execution, or release packaging.

See `SECURITY.md` for vulnerability reporting.

