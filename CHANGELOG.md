# Changelog

All notable changes to Midom Local Event Video Processor will be documented here.

This project uses release tags such as `v0.1.0`.

## [Unreleased]

- Added deterministic Storyboard FFmpeg Processing support alongside Event Video Processing.
- Added local FFmpeg render paths for storyboard overlay, trim/pass-through, soundtrack replacement, final assembly, and optimize/re-encode operations.
- Added local card video take rendering for `multicam_card_local_video_take` and `mediastoryboard_card_local_video_take` modes `one_image`, `two_image_fade`, and `voice_over_video`.
- Added `scene_audio` storyboard input support for local voice-over video rendering.
- Added storyboard media-processing capability advertisement, job validation, candidate filtering, job-scoped input downloading, and command-construction tests.

## [0.1.1] - 2026-07-17

- Changed Event Video Processing main-video normalization to scale-to-cover with center crop instead of fit-and-pad, removing black letterboxing from public event output.
- Updated advertised Event Video Processing capabilities to report `scale_to_cover_crop`.
- Added staff-friendly runtime controls: `Q` stops for now and keeps the saved pairing; `T` disconnects after confirmation and removes local credentials.

## [0.1.0] - 2026-07-16

Initial beta release.

- Added standalone local Event Video Processing worker.
- Added project pairing, durable worker token storage, worker status, log tailing, and disconnect commands.
- Added deterministic FFmpeg Event Video Processing for Midom Video Collection Events.
- Added bundled-release packaging script for Linux and Windows release artifacts.
- Added Linux release bundle generation with bundled FFmpeg/FFprobe.
- Added staff-friendly README, MIT license, and security reporting policy.
