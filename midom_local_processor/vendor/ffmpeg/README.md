# FFmpeg Runtime Binaries

This directory is reserved for optional packaged FFmpeg and FFprobe binaries.

Supported bundled platforms for this project:

- `linux-x86_64/ffmpeg`
- `linux-x86_64/ffprobe`
- `windows-x86_64/ffmpeg.exe`
- `windows-x86_64/ffprobe.exe`

macOS packaged binaries are intentionally not included in the first release.

The source checkout keeps these binary paths ignored by git. Release bundles
populate them from `.release-cache/` so ordinary operator installs include
FFmpeg. Development installs can still use FFmpeg from `PATH`, or explicit
binary paths via:

- `MIDOM_FFMPEG`
- `MIDOM_FFPROBE`

Release packaging currently downloads:

- Linux: FFmpeg 7.0.2 static build from `https://johnvansickle.com/ffmpeg/`,
  downloaded from `https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz`.
- Windows: FFmpeg 8.1.2 essentials build from `https://www.gyan.dev/ffmpeg/builds/`,
  downloaded from `https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip`.

Keep the upstream license and build provenance alongside packaged artifacts.
