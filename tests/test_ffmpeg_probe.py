from pathlib import Path

from midom_local_processor import ffmpeg_probe


def _touch(path: Path) -> str:
    path.write_text("", encoding="utf-8")
    return str(path)


def test_ffmpeg_binary_prefers_explicit_env_path(monkeypatch, tmp_path):
    ffmpeg = _touch(tmp_path / "custom-ffmpeg")
    monkeypatch.setenv("MIDOM_FFMPEG", ffmpeg)
    monkeypatch.setattr(ffmpeg_probe, "_bundled_binary", lambda name: None)
    monkeypatch.setattr(ffmpeg_probe.shutil, "which", lambda name: "/usr/bin/ffmpeg")

    assert ffmpeg_probe.ffmpeg_binary() == ffmpeg


def test_ffmpeg_binary_uses_bundled_before_path(monkeypatch, tmp_path):
    ffmpeg = _touch(tmp_path / "ffmpeg")
    monkeypatch.delenv("MIDOM_FFMPEG", raising=False)
    monkeypatch.setattr(ffmpeg_probe, "_platform_bin_dir", lambda: tmp_path)
    monkeypatch.setattr(ffmpeg_probe.shutil, "which", lambda name: "/usr/bin/ffmpeg")

    assert ffmpeg_probe.ffmpeg_binary() == ffmpeg


def test_ffprobe_binary_uses_sibling_of_explicit_ffmpeg(monkeypatch, tmp_path):
    ffmpeg = _touch(tmp_path / "ffmpeg")
    ffprobe = _touch(tmp_path / "ffprobe")
    monkeypatch.setenv("MIDOM_FFMPEG", ffmpeg)
    monkeypatch.delenv("MIDOM_FFPROBE", raising=False)
    monkeypatch.setattr(ffmpeg_probe, "_bundled_binary", lambda name: None)
    monkeypatch.setattr(ffmpeg_probe.shutil, "which", lambda name: None)

    assert ffmpeg_probe.ffprobe_binary() == ffprobe
