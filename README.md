# Midom Local Event Video Processor

Midom Local Event Video Processor is a small helper app for Midom Video Collection Events.

If you are helping run a campaign, PAC, advocacy group, public-interest project, community event, or field media collection effort, you may be asked to run this app on an office computer during an event window.

You do not need special media or software training to use it.

## What This Does

Midom Video Collection Events help teams collect real video from real places: interviews, field clips, community moments, public event footage, private documentary material, and other videos captured by staff or trusted volunteers.

The phone solved capture. It did not solve the workflow.

Videos still need to be received, organized, processed, reviewed, branded, captioned, published, archived, or saved for later production. Midom handles that project workflow. This local processor helps with one heavy part of the job: turning uploaded field video into a web-ready processed video.

When this app is running, Midom can send it a video processing task for the project it is paired with. The app downloads only the files needed for that one task, processes the video locally, sends the processed result back to Midom, and waits for the next task.

Midom still controls review, moderation, approval, publishing, public galleries, captions, and final attachment to the project. Running this app does not give your computer permission to publish videos or bypass review.

## Why You Might Be Asked To Run It

Video Collection Events can be bursty. A team may have quiet periods followed by many phone uploads during a rally, county fair, public meeting, canvassing day, ride, concert, volunteer shift, or community outreach event.

Those videos can be large. Processing them on Midom's hosted system works, but local office computers can help carry the load. If your computer is trusted by the project and has a stable internet connection, it can act like an extra processing station during the event.

This helps the organization keep the energy of live collection while preserving the safety pause: videos are processed quickly, but still reviewed before public use.

## What You Need

- A Windows or Linux computer.
- A stable internet connection.
- A download link for the Midom Local Event Video Processor package.
- A pairing code from a Midom project administrator.
- Permission from your organization to leave the processor running during the event.

macOS packages are not supported in the first release.

## Downloading The App

Use the download link provided by your project administrator or event coordinator.

If that link opens a page with a green `Code` button, ignore that button. Use the download file your coordinator pointed you to.

Use the release package meant for your computer:

- Windows: download the Windows `.zip` package.
- Linux: download the Linux `.tar.gz` package.

After downloading, unzip or extract the package into a normal folder, such as your Desktop or Documents folder.

## First Run

Your Midom project administrator will give you a pairing code. Pairing codes are short-lived setup codes, so use the code soon after it is created.

Open the starter file in the extracted processor folder.

Windows:

```text
start-local-processor.bat
```

Linux:

```text
start-local-processor.sh
```

The first time you run it, the processor will ask for:

- Midom address
- pairing code
- processor name

For the Midom address, most users can accept the suggested value:

```text
https://midombot.com
```

Paste the pairing code exactly as it was given to you.

If your coordinator gives you one complete command to paste instead, copy the whole command exactly.

After it starts, leave the window open. The app will wait for video processing jobs. Seeing messages that there are no jobs is normal when nobody has uploaded a video waiting for local processing.

If this first setup step is unfamiliar, ask the person coordinating the Midom event to help.

## Starting Later

After the first successful pairing, you usually do not need a new pairing code. Start the same processor again with:

Windows:

```bat
start-local-processor.bat
```

On Windows, you can usually double-click `start-local-processor.bat` after the first successful pairing.

Linux:

```bash
./start-local-processor.sh
```

The app stores a project-scoped worker token on your computer. That token is not your Midom password. It is only for this local processor and only for the paired project.

If Midom says the worker token expired, was revoked, or is no longer allowed, ask the project administrator for a new pairing code.

## During An Event

Keep the processor window open while the event is active.

The app may show messages like:

- waiting for jobs
- candidate poll returned no jobs
- starting claimed job
- downloading input
- processing video
- uploading processed MP4
- job completed

Those messages are normal. If the app reports an error repeatedly, copy the last few log lines and send them to the person supporting the event.

## What This App Does Not Do

This app does not publish videos. It does not approve videos. It does not decide what appears in a public gallery. It does not read arbitrary project files. It does not take over your computer.

It only connects outbound to Midom, accepts compatible processing jobs for the paired project, downloads the inputs for a claimed job, processes the video locally, uploads one processed MP4 result, and waits.

## For Technical Readers

The sections below describe the worker contract, local development, release packaging, command-line behavior, and security model.

## Capability Contract

The processor reports:

- `worker_kind = "local_media_processor"`
- `worker_display_name = "Local Event Video Processor"`
- `family = "media_processing"`
- `media_type = "video"`
- `processing_task = "event_video_processing"`
- `processor_id = "event_video_ffmpeg_processor"`
- `supported_output_profiles = ["mobile_public_720p"]`

The first-pass output profile produces exact canvases:

- Landscape: `1280x720`
- Portrait: `720x1280`

The source video is scaled to fit inside the exact canvas and padded. Transparent PNG overlays and bumper images are selected by output orientation.

## Install For Development

```bash
cd midom-local-event-video-processor
python -m venv .venv
. .venv/bin/activate
pip install -e .
```

FFmpeg and FFprobe are required. The processor looks for them in this order:

1. Explicit environment variables: `MIDOM_FFMPEG` and `MIDOM_FFPROBE`.
2. Packaged binaries under `midom_local_processor/vendor/ffmpeg/` for Linux x86_64 or Windows x86_64.
3. `ffmpeg` and `ffprobe` on `PATH`.

Release bundles include Linux x86_64 or Windows x86_64 FFmpeg/FFprobe binaries so non-technical users do not need to install FFmpeg separately. Those binaries remain under their upstream FFmpeg licenses, with provenance and checksums included in each release bundle.

macOS packaged binaries are not supported in the first release.

## Build Release Bundles

Release bundles are built with `scripts/build_release.py`. The script downloads the appropriate FFmpeg build, keeps it in `.release-cache/`, builds a local application bundle, and writes release artifacts to `release/`.

Install release tooling:

```bash
python3.10 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[release]'
```

Build the current platform bundle:

```bash
python scripts/build_release.py
```

Refresh FFmpeg during a security update:

```bash
python scripts/build_release.py --refresh-ffmpeg
```

Windows bundles must be built on Windows. Linux bundles must be built on Linux.

On Linux, use a Python 3.10+ build with shared-library support. Distro Python builds such as `/usr/bin/python3.10` usually work; static pyenv builds may not work with PyInstaller.

## Command-Line Start For Source Installs

Pairing codes are short-lived, one-time setup credentials. They are not event credentials and should not be extended for week-long events.

After successful pairing, Midom returns a project-scoped worker token. This local processor stores that token durably in its own config file so it can restart without a new pairing code until the worker token expires or is revoked.

For source/development installs, start the processor with a Midom pairing code:

```bash
midom-local-processor start \
  --url https://your-midom.example.com \
  --code PAIRING_CODE \
  --name "Office Processor"
```

After pairing once, restart the same source/development install with:

```bash
midom-local-processor start
```

For local development only:

```bash
midom-local-processor start \
  --url http://127.0.0.1:50002 \
  --code PAIRING_CODE \
  --name "Office Processor" \
  --allow-local-http
```

Local credentials are stored outside the source tree:

- Windows: `%APPDATA%/MidomLocalProcessor/config.json`
- Linux: `~/.config/midom-local-processor/config.json`

This config is specific to this local processor and must not be shared with other tools.

The worker:

1. Sends heartbeats.
2. Polls candidates.
3. Claims one compatible Event Video Processing job at a time.
4. Downloads only job-scoped inputs.
5. Runs FFmpeg.
6. Uploads exactly one MP4 artifact.
7. Completes or fails the job.

## Other Commands

```bash
midom-local-processor pair
midom-local-processor run
midom-local-processor status
midom-local-processor show-config
midom-local-processor update-capabilities
midom-local-processor disconnect
```

`disconnect` removes local credentials only after Midom accepts the remote disconnect. Use `--force-local` only when you intentionally want to discard local credentials after a remote disconnect failure.

If Midom rejects the stored worker token with HTTP 401 or 403, the token is probably expired, revoked, or no longer allowed for the project. Generate a fresh Midom pairing code and run `pair` again.

## Security Model

- HTTPS is required by default.
- HTTP requires explicit localhost or private LAN development flags.
- Worker token is project scoped.
- Worker token expiration is shown by `midom-local-processor status`.
- Token is redacted from display output.
- Inputs are downloaded only through claimed-job worker routes.
- `dbfileid` is never used to build download URLs.
- Arbitrary FFmpeg arguments are not accepted from Midom.
- Only the named `mobile_public_720p` profile is supported.
- Inputs and outputs are validated before upload.
