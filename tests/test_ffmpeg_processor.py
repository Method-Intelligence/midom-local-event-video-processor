from midom_local_processor import ffmpeg_processor
from midom_local_processor.ffmpeg_processor import EventVideoProcessor, LocalProcessJob, event_video_output_size, ffmpeg_progress_from_line


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
