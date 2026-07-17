from midom_local_processor import capabilities


def test_event_video_capabilities_advertise_cover_crop_scaling(monkeypatch):
    monkeypatch.setattr(
        capabilities,
        "probe_ffmpeg",
        lambda: {
            "ffmpeg_available": True,
            "ffprobe_available": True,
            "libx264_available": True,
            "quicktime_demux_available": True,
        },
    )

    payload = capabilities.build_capabilities()
    event_video = payload["media_processing"][0]

    assert event_video["worker_display_name"] == "Local Event Video Processor"
    assert event_video["display_name"] == "Event Video Processing"
    assert event_video["scaling_mode"] == "scale_to_cover_crop"
