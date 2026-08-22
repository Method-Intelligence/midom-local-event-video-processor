import subprocess

import pytest

from midom_local_processor import ffmpeg_processor
from midom_local_processor.ffmpeg_processor import EventVideoProcessor, LocalProcessJob, event_video_output_size, ffmpeg_progress_from_line
from midom_local_processor.ffmpeg_probe import ffmpeg_binary, probe_video_metadata
from midom_local_processor.types import DownloadedInput


def test_event_video_output_size_exact_landscape():
    assert event_video_output_size({"orientation": "landscape"}) == (1280, 720)


def test_event_video_output_size_exact_portrait():
    assert event_video_output_size({"orientation": "portrait"}) == (720, 1280)


def test_ffmpeg_progress_from_line_maps_duration_span():
    progress = ffmpeg_progress_from_line("out_time=00:00:05.000000", 10.0, 20, 80)
    assert progress == 50


def test_main_ffmpeg_filter_uses_cover_crop_for_source_video(monkeypatch, tmp_path):
    captured = {}

    def capture_command(self, job_id, command, **kwargs):
        captured["command"] = command

    monkeypatch.setattr(ffmpeg_processor, "ffmpeg_binary", lambda: "ffmpeg")
    monkeypatch.setattr(EventVideoProcessor, "_run_ffmpeg_with_progress", capture_command)

    processor = EventVideoProcessor()
    processor._run_main_ffmpeg(
        123,
        tmp_path / "source.mp4",
        tmp_path / "overlay.png",
        tmp_path / "output.mp4",
        {"has_audio": True, "duration_seconds": 10.0},
        720,
        1280,
        LocalProcessJob(),
    )

    command = captured["command"]
    filter_complex = command[command.index("-filter_complex") + 1]

    assert "[0:v]scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,setsar=1" in filter_complex
    assert "force_original_aspect_ratio=decrease" not in filter_complex
    assert "pad=720:1280" not in filter_complex
    assert "[vbase][ov]overlay=0:0:format=auto,format=yuv420p[vout]" in filter_complex


def downloaded_video(tmp_path, input_id=1, *, has_audio=True, duration=12.0, width=1920, height=1080, kind="source_video"):
    return DownloadedInput(
        input_id=input_id,
        kind=kind,
        path=tmp_path / f"input-{input_id}.mp4",
        mime_type="video/mp4",
        sha256="0" * 64,
        metadata={
            "duration_seconds": duration,
            "has_audio": has_audio,
            "width": width,
            "height": height,
            "display_width": width,
            "display_height": height,
        },
        category="video",
    )


def downloaded_audio(tmp_path, input_id=2, *, duration=10.0, kind="soundtrack_audio"):
    return DownloadedInput(
        input_id=input_id,
        kind=kind,
        path=tmp_path / f"input-{input_id}.m4a",
        mime_type="audio/mp4",
        sha256="0" * 64,
        metadata={"duration_seconds": duration, "audio_codec": "aac"},
        duration_seconds=duration,
        category="audio",
    )


def downloaded_image(tmp_path, input_id=3, *, kind="overlay_image", role="overlay", width=640, height=360):
    return DownloadedInput(
        input_id=input_id,
        kind=kind,
        path=tmp_path / f"input-{input_id}.png",
        mime_type="image/png",
        sha256="0" * 64,
        width=width,
        height=height,
        category="image",
        role=role,
        decoded_format="PNG",
    )


def capture_ffmpeg_command(monkeypatch):
    captured = {}

    def capture_command(self, job_id, command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs

    monkeypatch.setattr(ffmpeg_processor, "ffmpeg_binary", lambda: "ffmpeg")
    monkeypatch.setattr(EventVideoProcessor, "_run_ffmpeg_with_progress", capture_command)
    return captured


def test_storyboard_optimize_video_command_uses_storyboard_encode_args(monkeypatch, tmp_path):
    captured = capture_ffmpeg_command(monkeypatch)
    processor = EventVideoProcessor()

    processor._run_storyboard_optimize_video_ffmpeg(
        124,
        downloaded_video(tmp_path, has_audio=False),
        tmp_path / "optimized.mp4",
        1280,
        720,
        LocalProcessJob(),
        processing={"fps": 24, "crf": 25, "preset": "fast", "audio_bitrate": "96k"},
        progress_start=10,
        progress_end=90,
        status="Optimizing.",
    )

    command = captured["command"]
    filter_complex = command[command.index("-filter_complex") + 1]
    assert "anullsrc=channel_layout=stereo:sample_rate=48000" in command
    assert "scale=1280:720:force_original_aspect_ratio=decrease" in filter_complex
    assert "pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=black" in filter_complex
    assert "fps=24" in filter_complex
    assert command[command.index("-crf") + 1] == "25"
    assert command[command.index("-preset") + 1] == "fast"
    assert command[command.index("-b:a") + 1] == "96k"


def test_storyboard_replace_soundtrack_command_maps_external_audio(monkeypatch, tmp_path):
    captured = capture_ffmpeg_command(monkeypatch)
    processor = EventVideoProcessor()

    processor._run_storyboard_replace_soundtrack_ffmpeg(
        125,
        downloaded_video(tmp_path, input_id=1, duration=20.0),
        downloaded_audio(tmp_path, input_id=2, duration=30.0),
        tmp_path / "soundtrack.mp4",
        1280,
        720,
        LocalProcessJob(),
        processing={"output_duration_seconds": 6, "soundtrack_start_seconds": 1.5, "video_start_seconds": 2},
        progress_start=10,
        progress_end=90,
        status="Replacing.",
    )

    command = captured["command"]
    filter_complex = command[command.index("-filter_complex") + 1]
    assert command.count("-i") == 2
    assert "[1:a:0]atrim=start=1.500:duration=6.000" in filter_complex
    assert "[0:v]trim=start=2.000:duration=6.000" in filter_complex
    assert command[command.index("-t") + 1] == "6.000"
    assert command[command.index("-map") + 1] == "[vout]"


def test_storyboard_overlay_command_applies_luminance_matte(monkeypatch, tmp_path):
    captured = capture_ffmpeg_command(monkeypatch)
    processor = EventVideoProcessor()

    processor._run_storyboard_video_ffmpeg(
        126,
        downloaded_video(tmp_path, input_id=1, has_audio=True),
        downloaded_image(tmp_path, input_id=2, role="overlay", width=640, height=360),
        downloaded_image(tmp_path, input_id=3, kind="matte_image", role="matte", width=640, height=360),
        None,
        tmp_path / "overlay.mp4",
        1280,
        720,
        LocalProcessJob(),
        processing={"overlay_x": 10, "overlay_y": 20, "overlay_width": 320, "overlay_height": 180},
        progress_start=10,
        progress_end=90,
        status="Overlay.",
    )

    filter_complex = captured["command"][captured["command"].index("-filter_complex") + 1]
    assert "[2:v]scale=320:180,format=rgb24,format=gray[ovmask]" in filter_complex
    assert "[ovbase][ovmask]alphamerge,format=rgba[ovmatte]" in filter_complex
    assert "[vbase][ov]overlay=10:20:format=auto:eof_action=pass,format=yuv420p[vout]" in filter_complex


def test_storyboard_animated_overlay_command_uses_zoompan(monkeypatch, tmp_path):
    captured = capture_ffmpeg_command(monkeypatch)
    processor = EventVideoProcessor()

    processor._run_storyboard_video_ffmpeg(
        127,
        downloaded_video(tmp_path, input_id=1, has_audio=True),
        downloaded_image(tmp_path, input_id=2, role="overlay", width=640, height=360),
        None,
        None,
        tmp_path / "animated.mp4",
        1280,
        720,
        LocalProcessJob(),
        processing={
            "overlay_variant": "animated_rectangle",
            "overlay_preset": "bottom_right",
            "end_overlay_preset": "top_left",
            "overlay_scale": 0.25,
            "end_overlay_scale": 0.4,
            "fps": 30,
        },
        progress_start=10,
        progress_end=90,
        status="Animated overlay.",
    )

    filter_complex = captured["command"][captured["command"].index("-filter_complex") + 1]
    assert "zoompan=z=" in filter_complex
    assert "overlay=x=" in filter_complex
    assert "eval=frame" in filter_complex


def test_storyboard_local_one_image_command_adds_silent_audio(monkeypatch, tmp_path):
    captured = capture_ffmpeg_command(monkeypatch)
    processor = EventVideoProcessor()

    processor._run_storyboard_local_video_take_ffmpeg(
        128,
        [downloaded_image(tmp_path, input_id=4, kind="start_image", role="")],
        tmp_path / "one-image.mp4",
        1280,
        720,
        LocalProcessJob(),
        processing={"render_mode": "one_image", "duration_seconds": 3.5, "fps": 24},
        progress_start=10,
        progress_end=90,
        status="One image.",
    )

    command = captured["command"]
    filter_complex = command[command.index("-filter_complex") + 1]
    assert command.count("-i") == 2
    assert "anullsrc=channel_layout=stereo:sample_rate=48000" in command
    assert "[0:v]fps=24,scale=1280:720:force_original_aspect_ratio=decrease" in filter_complex
    assert "[1:a:0]aresample=48000,aformat=channel_layouts=stereo[aout]" in filter_complex
    assert command[command.index("-t") + 1] == "3.500"


def test_storyboard_local_two_image_fade_command_crossfades_images(monkeypatch, tmp_path):
    captured = capture_ffmpeg_command(monkeypatch)
    processor = EventVideoProcessor()

    processor._run_storyboard_local_video_take_ffmpeg(
        129,
        [
            downloaded_image(tmp_path, input_id=4, kind="start_image", role=""),
            downloaded_image(tmp_path, input_id=5, kind="end_image", role=""),
        ],
        tmp_path / "two-image.mp4",
        1280,
        720,
        LocalProcessJob(),
        processing={"render_mode": "two_image_fade", "duration_seconds": 5, "fade_start_seconds": 2, "fade_duration_seconds": 1.25},
        progress_start=10,
        progress_end=90,
        status="Two image.",
    )

    command = captured["command"]
    filter_complex = command[command.index("-filter_complex") + 1]
    assert command.count("-i") == 3
    assert "[vend]fade=t=in:st=2.000:d=1.250:alpha=1[vendfade]" in filter_complex
    assert "[vstart][vendfade]overlay=x=0:y=0:format=auto,format=yuv420p[vout]" in filter_complex
    assert "anullsrc=channel_layout=stereo:sample_rate=48000" in command


def test_storyboard_local_voice_over_video_command_maps_scene_audio(monkeypatch, tmp_path):
    captured = capture_ffmpeg_command(monkeypatch)
    processor = EventVideoProcessor()

    processor._run_storyboard_voice_over_video_take_ffmpeg(
        130,
        downloaded_video(tmp_path, input_id=6, duration=8.0),
        downloaded_audio(tmp_path, input_id=7, duration=4.2, kind="scene_audio"),
        tmp_path / "voice-over.mp4",
        1280,
        720,
        LocalProcessJob(),
        processing={"render_mode": "voice_over_video", "duration_seconds": 4.0, "scene_audio_start_seconds": 0.25, "fps": 30},
        progress_start=10,
        progress_end=90,
        status="Voice over.",
    )

    command = captured["command"]
    filter_complex = command[command.index("-filter_complex") + 1]
    assert command.count("-i") == 2
    assert command[command.index("-ss") + 1] == "0.250"
    assert "[1:a:0]aresample=48000,aformat=channel_layouts=stereo[aout]" in filter_complex
    assert command[command.index("-t") + 1] == "4.000"
    assert command[command.index("-movflags") + 1] == "+faststart"


def test_storyboard_local_voice_over_video_rejects_short_scene_audio(monkeypatch, tmp_path):
    capture_ffmpeg_command(monkeypatch)
    processor = EventVideoProcessor()

    try:
        processor._run_storyboard_voice_over_video_take_ffmpeg(
            131,
            downloaded_video(tmp_path, input_id=6, duration=8.0),
            downloaded_audio(tmp_path, input_id=7, duration=3.7, kind="scene_audio"),
            tmp_path / "voice-over.mp4",
            1280,
            720,
            LocalProcessJob(),
            processing={"render_mode": "voice_over_video", "duration_seconds": 4.0},
            progress_start=10,
            progress_end=90,
            status="Voice over.",
        )
    except ValueError as exc:
        assert "scene_audio is shorter" in str(exc)
    else:
        raise AssertionError("expected short scene_audio validation failure")


def test_storyboard_segment_normalize_command_uses_cover_crop_and_silent_audio(monkeypatch, tmp_path):
    captured = capture_ffmpeg_command(monkeypatch)
    processor = EventVideoProcessor()

    processor._run_storyboard_segment_normalize_ffmpeg(
        132,
        downloaded_video(tmp_path, input_id=8, has_audio=False, duration=2.0, width=640, height=480),
        tmp_path / "segment.mp4",
        1280,
        720,
        LocalProcessJob(),
        processing={"resize_mode": "scale_to_cover_crop", "fps": 30, "crf": 20, "preset": "veryfast", "audio_bitrate": "128k"},
        progress_start=10,
        progress_end=90,
        status="Normalize.",
    )

    command = captured["command"]
    filter_complex = command[command.index("-filter_complex") + 1]
    assert "anullsrc=channel_layout=stereo:sample_rate=48000" in command
    assert "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,fps=30,setsar=1,format=yuv420p[vout]" in filter_complex
    assert "[1:a:0]aresample=48000,aformat=channel_layouts=stereo[aout]" in filter_complex
    assert command[command.index("-crf") + 1] == "20"
    assert command[command.index("-preset") + 1] == "veryfast"
    assert command[command.index("-b:a") + 1] == "128k"
    assert command[command.index("-t") + 1] == "2.000"


def create_no_audio_source(path, *, width=640, height=480, duration=0.5):
    command = [
        ffmpeg_binary(),
        "-y",
        "-hide_banner",
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size={width}x{height}:rate=15",
        "-t",
        str(duration),
        "-an",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(path),
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=30)
    if completed.returncode != 0:
        pytest.skip(f"ffmpeg could not generate smoke input: {completed.stderr or completed.stdout}")


def test_storyboard_segment_normalize_smoke_adds_audio_for_no_audio_mp4(tmp_path):
    source_path = tmp_path / "source-no-audio.mp4"
    output_path = tmp_path / "normalized.mp4"
    create_no_audio_source(source_path)
    metadata = probe_video_metadata(source_path)
    assert metadata["has_audio"] is False

    processor = EventVideoProcessor()
    processor._run_storyboard_segment_normalize_ffmpeg(
        133,
        DownloadedInput(
            input_id=9,
            kind="source_video",
            path=source_path,
            mime_type="video/mp4",
            sha256="0" * 64,
            metadata=metadata,
            category="video",
            role="raw_segment",
        ),
        output_path,
        1280,
        720,
        LocalProcessJob(),
        processing={"resize_mode": "scale_to_cover_crop", "fps": 30, "crf": 20, "preset": "veryfast", "audio_bitrate": "128k"},
        progress_start=10,
        progress_end=90,
        status="Normalize.",
    )

    output_metadata = probe_video_metadata(output_path)
    assert output_metadata["display_width"] == 1280
    assert output_metadata["display_height"] == 720
    assert output_metadata["video_codec"] == "h264"
    assert output_metadata["audio_codec"] == "aac"
    assert output_metadata["has_audio"] is True
