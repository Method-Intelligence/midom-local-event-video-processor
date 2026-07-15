# Release Process

This document describes how to prepare a Midom Local Event Video Processor release.

The source repository should remain small. Do not commit release bundles, virtual environments, build directories, `.release-cache/`, or raw FFmpeg binaries.

## Release Artifacts

Each release should provide:

- `midom-local-processor-VERSION-linux-x86_64.tar.gz`
- `midom-local-processor-VERSION-linux-x86_64.tar.gz.sha256`
- `midom-local-processor-VERSION-windows-x86_64.zip`
- `midom-local-processor-VERSION-windows-x86_64.zip.sha256`

Linux bundles must be built on Linux. Windows bundles must be built on Windows.

## Before Building

1. Confirm the version in `pyproject.toml`.
2. Update `CHANGELOG.md`.
3. Run tests:

   ```bash
   PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 python -m pytest -q tests -p no:cacheprovider
   ```

4. Run compile check:

   ```bash
   PYTHONPYCACHEPREFIX=/tmp/midom-local-processor-pycache python -m compileall midom_local_processor scripts
   ```

5. Confirm git does not include local credentials, caches, build outputs, release bundles, or raw FFmpeg binaries.

## Linux Build

Use a Python 3.10+ build with shared-library support. On this workstation, `/usr/bin/python3.10` works; static pyenv Python builds may not work with PyInstaller.

```bash
/usr/bin/python3.10 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[release]'
python scripts/build_release.py --platform linux-x86_64
```

To refresh FFmpeg during a security or dependency update:

```bash
python scripts/build_release.py --platform linux-x86_64 --refresh-ffmpeg
```

Smoke test:

```bash
dist/pyinstaller/midom-local-processor/midom-local-processor --help
MIDOM_LOCAL_PROCESSOR_CONFIG_DIR=/tmp/midom-local-processor-smoke \
  dist/pyinstaller/midom-local-processor/midom-local-processor status
```

The `status` command should report that no config exists. That is expected for an unpaired smoke test.

## Windows Build

Build on Windows 11.

Use Python 3.10 or newer from python.org or another normal shared-library Python distribution.

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[release]"
python scripts\build_release.py --platform windows-x86_64
```

To refresh FFmpeg:

```powershell
python scripts\build_release.py --platform windows-x86_64 --refresh-ffmpeg
```

Smoke test:

```powershell
dist\pyinstaller\midom-local-processor\midom-local-processor.exe --help
$env:MIDOM_LOCAL_PROCESSOR_CONFIG_DIR="$env:TEMP\midom-local-processor-smoke"
dist\pyinstaller\midom-local-processor\midom-local-processor.exe status
```

The `status` command should report that no config exists. That is expected for an unpaired smoke test.

## Live Release Test

Before announcing a release:

1. Download or copy the built release artifact to a clean test directory.
2. Extract it.
3. Pair with a Midom test project using a fresh pairing code.
4. Start the processor from the release launcher.
5. Queue a Video Collection Event processing job.
6. Confirm the processor claims the job.
7. Confirm the processed MP4 uploads and Midom accepts completion.
8. Confirm restart works without a new pairing code.
9. Confirm disconnect/revoke behavior.

## GitHub Release

Create a GitHub release tagged as `vVERSION`.

Attach the Linux and Windows artifacts plus their `.sha256` files.

Suggested release notes:

```text
Midom Local Event Video Processor VERSION

For event staff:
- Download the Windows zip or Linux tar.gz for your computer.
- Extract the package.
- Use the pairing code provided by your Midom project administrator.
- Leave the processor window open during the event.

Included:
- Standalone Midom local Event Video Processing worker.
- Bundled FFmpeg and FFprobe.
- Project-scoped worker pairing.
- One-job-at-a-time local video processing for Midom Video Collection Events.

Security:
- The processor connects outbound to Midom.
- It can only download inputs for jobs it has claimed.
- Midom remains authoritative for review, moderation, approval, publication, and artifact validation.
```

## After Release

- Verify the public release page shows the correct artifacts.
- Download each artifact from GitHub and verify checksum.
- Keep the local `.release-cache/` if you expect to rebuild the same artifacts.
- Do not commit `.release-cache/`, `release/`, `build/`, or `dist/`.

