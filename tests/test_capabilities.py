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

    storyboard = payload["media_processing"][1]
    assert storyboard["worker_display_name"] == "Local Event Video Processor"
    assert storyboard["display_name"] == "Storyboard FFmpeg Processing"
    assert storyboard["processing_task"] == "storyboard_ffmpeg_processing"
    assert storyboard["processor_id"] == "storyboard_ffmpeg_processor"
    assert "multicam_card_overlay_take" in storyboard["supported_operations"]
    assert "replace_video_soundtrack" in storyboard["supported_operations"]
    assert "multicam_final_assembly" in storyboard["supported_operations"]
    assert "segmented_media_segment_normalize" in storyboard["supported_operations"]
    assert "soundtrack_video_container_audio" in storyboard["operation_features"]["replace_video_soundtrack"]
    assert storyboard["operation_features"]["segmented_media_segment_normalize"] == [
        "single_segment_normalize",
        "scale_to_cover_crop",
        "exact_target_dimensions",
        "fps_normalize",
        "h264_aac_mp4_faststart",
        "silent_audio_fill",
    ]
    assert "multicam_card_local_video_take" in storyboard["supported_operations"]
    assert "mediastoryboard_card_local_video_take" in storyboard["supported_operations"]
    assert "voice_over_video" in storyboard["operation_features"]["multicam_card_local_video_take"]
    assert storyboard["input_mime_types"]["scene_audio"] == ["audio/wav", "audio/x-wav", "audio/mpeg", "audio/mp4", "audio/webm", "audio/ogg"]
