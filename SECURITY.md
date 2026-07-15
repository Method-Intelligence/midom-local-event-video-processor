# Security Policy

## Supported Use

Midom Local Event Video Processor is a local worker that connects outbound to a Midom server and processes project-scoped Event Video Processing jobs with FFmpeg.

It is intended to run only on machines controlled by trusted project members or event operators.

## Trust Model

The processor is not an administrator for Midom.

A paired processor receives a worker token scoped to one Midom worker, user, organization, and project. That token is used only for Midom media-worker routes such as heartbeat, candidate polling, job claim, input download, progress, artifact upload, complete, fail, and disconnect.

The processor cannot publish event videos, approve moderation, bypass review, or access arbitrary project files. Midom remains authoritative and validates uploaded artifacts server-side.

## Local Credential Storage

After pairing, the processor stores a project-scoped worker token in the local user config directory.

Default config locations:

- Windows: `%APPDATA%/MidomLocalProcessor/config.json`
- Linux: `~/.config/midom-local-processor/config.json`

Protect this file like a credential. Do not share it or commit it to source control.

If a token is exposed, revoke or disconnect the worker in Midom and pair again with a new code.

## Network Security

HTTPS is required by default.

HTTP is allowed only with explicit local development flags:

- `--allow-local-http`
- `--allow-lan-http`

Do not use HTTP for production or public networks.

## FFmpeg Safety

The processor does not accept arbitrary FFmpeg command lines from Midom.

It supports a fixed Event Video Processing profile and validates input kinds, MIME types, file sizes, dimensions, duration, and output format before upload.

Packaged releases may include FFmpeg and FFprobe binaries for Linux x86_64 and Windows x86_64. Use only release artifacts from a trusted source, and preserve FFmpeg license/build provenance with any packaged binaries.

Only run this processor against a Midom server you trust.

## Reporting Vulnerabilities

Please report security issues privately to `security@methodintelligence.io`.

Do not open public GitHub issues for vulnerabilities until we have had a chance to investigate and release a fix.

When reporting, include:

- Processor version or commit
- Operating system
- Midom deployment type, if relevant
- Steps to reproduce
- Logs with tokens and pairing codes removed

## Scope

In scope:

- Worker token exposure or misuse
- Access to files outside claimed job inputs
- Artifact upload or validation bypass
- Unsafe FFmpeg argument injection
- Cross-project or cross-worker authorization issues
- Insecure default network behavior

Out of scope:

- Issues caused by running untrusted FFmpeg binaries
- Local machine compromise unrelated to this processor
- Midom server vulnerabilities not involving this worker
- Denial of service from intentionally running many local workers on one machine
