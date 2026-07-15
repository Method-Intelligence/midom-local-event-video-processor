from midom_local_processor.ffmpeg_processor import event_video_output_size, ffmpeg_progress_from_line


def test_event_video_output_size_exact_landscape():
    assert event_video_output_size({"orientation": "landscape"}) == (1280, 720)


def test_event_video_output_size_exact_portrait():
    assert event_video_output_size({"orientation": "portrait"}) == (720, 1280)


def test_ffmpeg_progress_from_line_maps_duration_span():
    progress = ffmpeg_progress_from_line("out_time=00:00:05.000000", 10.0, 20, 80)
    assert progress == 50
