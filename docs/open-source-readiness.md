# Open Source Readiness Checklist

Use this checklist before making the repository public or publishing a release.

## Repository

- MIT license is present.
- `SECURITY.md` has a private reporting address.
- `README.md` explains the project for non-technical event staff before technical details.
- `CONTRIBUTING.md`, `SUPPORT.md`, and `CHANGELOG.md` are present.
- No local config files or worker tokens are committed.
- No pairing codes are committed.
- No generated release bundles are committed.
- No raw FFmpeg binaries are committed to source history.
- `.gitignore` excludes `.venv/`, `.release-cache/`, `build/`, `dist/`, `release/`, cache folders, and raw FFmpeg binary paths.

## Functionality

- Tests pass.
- Compile check passes.
- Linux release bundle builds.
- Linux release executable starts and shows help.
- Windows release bundle builds on Windows.
- Windows release executable starts and shows help.
- A release-bundle live test completes at least one Event Video Processing job.
- Restart without a new pairing code works after first pairing.

## Security

- HTTPS is required by default.
- HTTP requires explicit local/LAN development flags.
- Stored worker token is redacted from display output.
- Input downloads use canonical claimed-job input routes, not `dbfileid`.
- Worker cannot publish, approve, moderate, or bypass Midom review.
- FFmpeg commands are fixed by local profiles, not arbitrary server-supplied command lines.
- Release artifacts include FFmpeg provenance and checksum files.

## Midom Server Expectations

Before wide use, Midom should treat local processors as untrusted project-scoped compute and provide:

- Explicit event/project opt-in for local worker routing.
- Hosted fallback when local workers are busy, stale, paused, unhealthy, revoked, or timing out.
- Claim lease expiration and requeue/fallback behavior.
- Worker failure/timeout health tracking.
- Strict returned MP4 validation.
- Audit trail for claim, input download, upload, completion, failure, timeout, and fallback.
- Easy admin revoke/disconnect/requeue controls.

## Known First-Release Limitations

- Windows and Linux packages only.
- macOS packaging is not supported.
- Windows release may trigger SmartScreen or antivirus warnings until the project has reputation or code signing.
- Local processors are appropriate for trusted project/event participants, not arbitrary public volunteers.
- Once a local processor legitimately downloads claimed job inputs, Midom cannot technically prevent that local machine from copying them elsewhere.

