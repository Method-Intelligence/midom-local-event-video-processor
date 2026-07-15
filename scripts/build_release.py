#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / ".release-cache"
RELEASE_DIR = ROOT / "release"
PYINSTALLER_BUILD_DIR = ROOT / "build" / "pyinstaller"
PYINSTALLER_DIST_DIR = ROOT / "dist" / "pyinstaller"
APP_NAME = "midom-local-processor"
DISPLAY_NAME = "Midom Local Event Video Processor"


@dataclass(frozen=True)
class FfmpegBuild:
    platform_id: str
    url: str
    archive_name: str
    archive_type: str
    root_dir: str
    ffmpeg_member: str
    ffprobe_member: str
    license_members: tuple[str, ...]


FFMPEG_BUILDS = {
    "linux-x86_64": FfmpegBuild(
        platform_id="linux-x86_64",
        url="https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz",
        archive_name="ffmpeg-release-amd64-static.tar.xz",
        archive_type="tar.xz",
        root_dir="ffmpeg-7.0.2-amd64-static",
        ffmpeg_member="ffmpeg",
        ffprobe_member="ffprobe",
        license_members=("GPLv3.txt",),
    ),
    "windows-x86_64": FfmpegBuild(
        platform_id="windows-x86_64",
        url="https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
        archive_name="ffmpeg-release-essentials.zip",
        archive_type="zip",
        root_dir="ffmpeg-8.1.2-essentials_build",
        ffmpeg_member="bin/ffmpeg.exe",
        ffprobe_member="bin/ffprobe.exe",
        license_members=("LICENSE", "README.txt"),
    ),
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build local release bundles for Midom Local Event Video Processor.")
    parser.add_argument("--platform", choices=["auto", *FFMPEG_BUILDS.keys()], default="auto")
    parser.add_argument("--version", default=project_version())
    parser.add_argument("--refresh-ffmpeg", action="store_true", help="Redownload and re-extract FFmpeg for the selected platform.")
    parser.add_argument("--keep-stage", action="store_true", help="Keep the staging directory after archiving.")
    args = parser.parse_args()

    platform_id = detect_platform() if args.platform == "auto" else args.platform
    if platform_id not in FFMPEG_BUILDS:
        raise SystemExit(f"Unsupported release platform: {platform_id}")

    build = FFMPEG_BUILDS[platform_id]
    ffmpeg_dir = ensure_ffmpeg(build, refresh=args.refresh_ffmpeg)
    RELEASE_DIR.mkdir(parents=True, exist_ok=True)
    stage_dir = RELEASE_DIR / f"{APP_NAME}-{args.version}-{platform_id}"
    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    stage_dir.mkdir(parents=True)

    stage_executable_bundle(stage_dir, ffmpeg_dir, build)

    write_quickstart(stage_dir, build)
    archive_path = archive_stage(stage_dir, args.version, build)
    checksum_path = write_sha256(archive_path)
    if not args.keep_stage:
        shutil.rmtree(stage_dir)

    print(f"Release bundle: {archive_path}")
    print(f"Checksum: {checksum_path}")
    return 0


def project_version() -> str:
    in_project = False
    for line in (ROOT / "pyproject.toml").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped == "[project]":
            in_project = True
            continue
        if in_project and stripped.startswith("["):
            break
        if in_project and stripped.startswith("version"):
            _, value = stripped.split("=", 1)
            return value.strip().strip('"').strip("'")
    raise ValueError("Could not read project version from pyproject.toml")


def detect_platform() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if machine not in {"x86_64", "amd64"}:
        raise SystemExit(f"Unsupported machine architecture for release packaging: {machine}")
    if system == "linux":
        return "linux-x86_64"
    if system == "windows":
        return "windows-x86_64"
    raise SystemExit(f"Unsupported operating system for release packaging: {platform.system()}")


def ensure_ffmpeg(build: FfmpegBuild, *, refresh: bool) -> Path:
    target_dir = CACHE_DIR / "ffmpeg" / build.platform_id
    archive_path = CACHE_DIR / "downloads" / build.archive_name
    ffmpeg_name = "ffmpeg.exe" if build.platform_id.startswith("windows") else "ffmpeg"
    ffprobe_name = "ffprobe.exe" if build.platform_id.startswith("windows") else "ffprobe"
    if refresh and target_dir.exists():
        shutil.rmtree(target_dir)
    if (target_dir / ffmpeg_name).is_file() and (target_dir / ffprobe_name).is_file():
        return target_dir

    archive_path.parent.mkdir(parents=True, exist_ok=True)
    if refresh or not archive_path.is_file():
        print(f"Downloading {build.url}", flush=True)
        with urllib.request.urlopen(build.url) as response, archive_path.open("wb") as writer:
            shutil.copyfileobj(response, writer)

    extract_dir = CACHE_DIR / "extract" / build.platform_id
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir(parents=True)
    if build.archive_type == "tar.xz":
        with tarfile.open(archive_path, "r:xz") as archive:
            safe_extract_tar(archive, extract_dir)
    elif build.archive_type == "zip":
        with zipfile.ZipFile(archive_path) as archive:
            archive.extractall(extract_dir)
    else:  # pragma: no cover
        raise ValueError(f"Unsupported archive type: {build.archive_type}")

    source_root = extract_dir / build.root_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    copy_executable(source_root / build.ffmpeg_member, target_dir / ffmpeg_name)
    copy_executable(source_root / build.ffprobe_member, target_dir / ffprobe_name)
    for member in build.license_members:
        source = source_root / member
        if source.is_file():
            shutil.copy2(source, target_dir / source.name)
    write_ffmpeg_manifest(target_dir, build, archive_path)
    return target_dir


def safe_extract_tar(archive: tarfile.TarFile, target_dir: Path) -> None:
    target_root = target_dir.resolve()
    for member in archive.getmembers():
        member_target = (target_dir / member.name).resolve()
        if target_root not in {member_target, *member_target.parents}:
            raise ValueError(f"Refusing unsafe archive member path: {member.name}")
        if member.issym() or member.islnk():
            raise ValueError(f"Refusing archive link member: {member.name}")
    archive.extractall(target_dir)


def copy_executable(source: Path, target: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(source)
    shutil.copy2(source, target)
    if os.name != "nt":
        target.chmod(0o755)


def write_ffmpeg_manifest(target_dir: Path, build: FfmpegBuild, archive_path: Path) -> None:
    lines = [
        "FFmpeg bundle provenance",
        "========================",
        "",
        f"Platform: {build.platform_id}",
        f"Source URL: {build.url}",
        f"Downloaded archive: {archive_path.name}",
        f"Downloaded archive SHA-256: {sha256_file(archive_path)}",
        "",
        "Bundled file SHA-256:",
    ]
    for file_path in sorted(path for path in target_dir.iterdir() if path.is_file()):
        lines.append(f"{sha256_file(file_path)}  {file_path.name}")
    (target_dir / "PROVENANCE.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def stage_executable_bundle(stage_dir: Path, ffmpeg_dir: Path, build: FfmpegBuild) -> None:
    if importlib.util.find_spec("PyInstaller") is None:
        raise SystemExit(
            "PyInstaller is required for executable release bundles. "
            "Install with: python -m pip install -e '.[release]'"
        )
    add_data_sep = ";" if os.name == "nt" else ":"
    data_dest = f"midom_local_processor/vendor/ffmpeg/{build.platform_id}"
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--name",
        APP_NAME,
        "--distpath",
        str(PYINSTALLER_DIST_DIR),
        "--workpath",
        str(PYINSTALLER_BUILD_DIR),
        "--specpath",
        str(PYINSTALLER_BUILD_DIR),
        "--add-data",
        f"{ffmpeg_dir}{add_data_sep}{data_dest}",
        *pyinstaller_runtime_args(add_data_sep),
        str(ROOT / "midom_local_processor" / "pyinstaller_entry.py"),
    ]
    run(command)
    produced_dir = PYINSTALLER_DIST_DIR / APP_NAME
    if not produced_dir.is_dir():
        raise FileNotFoundError(produced_dir)
    shutil.copytree(produced_dir, stage_dir / APP_NAME)
    write_launchers(stage_dir)


def pyinstaller_runtime_args(add_data_sep: str) -> list[str]:
    args = [
        "--hidden-import",
        "ssl",
        "--hidden-import",
        "_ssl",
        "--hidden-import",
        "_hashlib",
    ]
    if os.name != "nt":
        return args

    for dll_path in windows_ssl_runtime_files():
        args.extend(["--add-binary", f"{dll_path}{add_data_sep}."])
    return args


def windows_ssl_runtime_files() -> list[Path]:
    search_roots = []
    for text in {sys.prefix, sys.base_prefix, sys.exec_prefix, str(Path(sys.executable).resolve().parent)}:
        if text:
            root = Path(text)
            search_roots.extend(
                [
                    root,
                    root / "DLLs",
                    root / "Library" / "bin",
                    root.parent,
                    root.parent / "Library" / "bin",
                ]
            )

    patterns = [
        "_ssl*.pyd",
        "_hashlib*.pyd",
        "libssl*.dll",
        "libcrypto*.dll",
        "ssleay32.dll",
        "libeay32.dll",
    ]
    found: dict[str, Path] = {}
    for root in search_roots:
        if not root.is_dir():
            continue
        for pattern in patterns:
            for candidate in root.glob(pattern):
                if candidate.is_file():
                    found[str(candidate.resolve()).lower()] = candidate.resolve()
    return sorted(found.values(), key=lambda path: str(path).lower())


def write_launchers(stage_dir: Path) -> None:
    linux_target = f'"$DIR/{APP_NAME}/{APP_NAME}"'
    windows_target = f'%~dp0{APP_NAME}\\{APP_NAME}.exe'

    shell = stage_dir / "start-local-processor.sh"
    shell.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
        f"exec {linux_target} start \"$@\"\n",
        encoding="utf-8",
    )
    shell.chmod(0o755)

    (stage_dir / "start-local-processor.bat").write_text(
        "@echo off\r\n"
        "setlocal\r\n"
        f"\"{windows_target}\" start %*\r\n"
        "if errorlevel 1 (\r\n"
        "  echo.\r\n"
        "  echo The processor stopped with an error. You can copy the message above for support.\r\n"
        "  pause\r\n"
        ")\r\n",
        encoding="utf-8",
    )


def write_quickstart(stage_dir: Path, build: FfmpegBuild) -> None:
    (stage_dir / "QUICKSTART.txt").write_text(
        f"{DISPLAY_NAME}\n"
        f"{'=' * len(DISPLAY_NAME)}\n\n"
        "This bundle includes FFmpeg and FFprobe for local Event Video Processing.\n\n"
        "First run:\n"
        "  Windows: open start-local-processor.bat\n"
        "  Linux: ./start-local-processor.sh\n\n"
        "The processor will ask for the Midom address, pairing code, and processor name.\n"
        "Most users can accept the suggested Midom address: https://midombot.com\n\n"
        "If your coordinator gives you one complete command to paste, use that exact command.\n\n"
        "After the first successful pairing:\n"
        "  Open the same starter file again. You usually do not need a new pairing code.\n\n"
        "Windows:\n"
        "  Use start-local-processor.bat from Command Prompt or PowerShell.\n\n"
        "Linux:\n"
        "  Use ./start-local-processor.sh from a terminal.\n\n"
        "Security:\n"
        "  Pairing codes are short-lived. The stored worker token is project-scoped and remains on this computer.\n\n"
        f"Bundle platform: {build.platform_id}\n",
        encoding="utf-8",
    )


def archive_stage(stage_dir: Path, version: str, build: FfmpegBuild) -> Path:
    base_name = f"{APP_NAME}-{version}-{build.platform_id}"
    if build.platform_id.startswith("windows"):
        archive_path = RELEASE_DIR / f"{base_name}.zip"
        if archive_path.exists():
            archive_path.unlink()
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path in sorted(stage_dir.rglob("*")):
                archive.write(path, path.relative_to(stage_dir.parent))
        return archive_path

    archive_path = RELEASE_DIR / f"{base_name}.tar.gz"
    if archive_path.exists():
        archive_path.unlink()
    with tarfile.open(archive_path, "w:gz") as archive:
        archive.add(stage_dir, arcname=stage_dir.name)
    return archive_path


def write_sha256(path: Path) -> Path:
    checksum_path = path.with_suffix(path.suffix + ".sha256")
    checksum_path.write_text(f"{sha256_file(path)}  {path.name}\n", encoding="utf-8")
    return checksum_path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as reader:
        for chunk in iter(lambda: reader.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(command: list[str]) -> None:
    print("+ " + " ".join(command))
    subprocess.run(command, cwd=ROOT, check=True)


if __name__ == "__main__":
    raise SystemExit(main())
