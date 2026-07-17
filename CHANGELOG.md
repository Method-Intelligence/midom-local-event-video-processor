# Changelog

All notable changes to Midom Local Event Video Processor will be documented here.

This project uses release tags such as `v0.1.0`.

## [Unreleased]

No unreleased changes yet.

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
