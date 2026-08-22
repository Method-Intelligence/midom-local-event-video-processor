from midom_local_processor.validation import validate_event_job, validate_storyboard_ffmpeg_job


def base_job():
    return {
        "family": "media_processing",
        "media_type": "video",
        "processing_task": "event_video_processing",
        "processor_id": "event_video_ffmpeg_processor",
        "output": {"count": 1, "format": "mp4", "profile": "mobile_public_720p"},
        "processing": {
            "normalize": True,
            "optimize_for_web": True,
            "apply_overlay": True,
            "add_ending_bumper": True,
            "target_max_width": 720,
            "target_max_height": 1280,
            "preserve_orientation": True,
        },
        "inputs": [
            {"input_id": 1, "kind": "source_video"},
            {"input_id": 2, "kind": "overlay_png", "orientation": "portrait"},
            {"input_id": 3, "kind": "bumper_image", "orientation": "portrait"},
        ],
    }


def test_validate_event_job_accepts_first_pass_contract():
    processing = validate_event_job(base_job())
    assert processing["apply_overlay"] is True


def test_validate_event_job_rejects_missing_overlay_when_enabled():
    job = base_job()
    job["inputs"] = [item for item in job["inputs"] if item["kind"] != "overlay_png"]
    try:
        validate_event_job(job)
    except ValueError as exc:
        assert "overlay_png" in str(exc)
    else:
        raise AssertionError("expected overlay validation failure")


def storyboard_job(operation_type="multicam_card_overlay_take"):
    return {
        "family": "media_processing",
        "media_type": "video",
        "processing_task": "storyboard_ffmpeg_processing",
        "processor_id": "storyboard_ffmpeg_processor",
        "operation_type": operation_type,
        "output": {"count": 1, "format": "mp4", "width": 1280, "height": 720},
        "processing": {
            "overlay_variant": "static_rectangle",
            "overlay_x": 10,
            "overlay_y": 20,
            "overlay_width": 320,
            "overlay_height": 180,
        },
        "inputs": [
            {"input_id": 10, "kind": "source_video"},
            {"input_id": 11, "kind": "overlay_image", "role": "overlay"},
            {"input_id": 12, "kind": "matte_image", "role": "matte"},
        ],
    }


def test_validate_storyboard_ffmpeg_job_accepts_overlay_contract():
    processing = validate_storyboard_ffmpeg_job(storyboard_job())
    assert processing["operation_type"] == "multicam_card_overlay_take"
    assert processing["output_width"] == 1280
    assert processing["output_height"] == 720


def test_validate_storyboard_ffmpeg_job_accepts_replace_soundtrack_contract():
    job = storyboard_job("replace_video_soundtrack")
    job["inputs"] = [
        {"input_id": 10, "kind": "source_video"},
        {"input_id": 13, "kind": "soundtrack_audio"},
    ]

    processing = validate_storyboard_ffmpeg_job(job)
    assert processing["operation_type"] == "replace_video_soundtrack"


def test_validate_storyboard_ffmpeg_job_rejects_replace_soundtrack_without_audio():
    job = storyboard_job("replace_video_soundtrack")
    job["inputs"] = [{"input_id": 10, "kind": "source_video"}]

    try:
        validate_storyboard_ffmpeg_job(job)
    except ValueError as exc:
        assert "soundtrack audio" in str(exc)
    else:
        raise AssertionError("expected soundtrack audio validation failure")


def local_take_job(render_mode="one_image"):
    inputs = [{"input_id": 20, "kind": "start_image"}]
    if render_mode == "two_image_fade":
        inputs.append({"input_id": 21, "kind": "end_image"})
    if render_mode == "voice_over_video":
        inputs = [
            {"input_id": 22, "kind": "source_video", "role": "visual"},
            {"input_id": 23, "kind": "scene_audio", "role": "voice_over"},
        ]
    return {
        "family": "media_processing",
        "media_type": "video",
        "processing_task": "storyboard_ffmpeg_processing",
        "processor_id": "storyboard_ffmpeg_processor",
        "operation_type": "multicam_card_local_video_take",
        "output": {"count": 1, "format": "mp4", "width": 1280, "height": 720},
        "processing": {"render_mode": render_mode, "duration_seconds": 4.0},
        "inputs": inputs,
    }


def test_validate_storyboard_local_take_accepts_one_image():
    processing = validate_storyboard_ffmpeg_job(local_take_job("one_image"))
    assert processing["operation_type"] == "multicam_card_local_video_take"
    assert processing["render_mode"] == "one_image"


def test_validate_storyboard_local_take_accepts_two_image_fade():
    processing = validate_storyboard_ffmpeg_job(local_take_job("two_image_fade"))
    assert processing["render_mode"] == "two_image_fade"


def test_validate_storyboard_local_take_accepts_voice_over_video():
    processing = validate_storyboard_ffmpeg_job(local_take_job("voice_over_video"))
    assert processing["render_mode"] == "voice_over_video"
    assert processing["duration_seconds"] == 4.0


def test_validate_storyboard_local_take_rejects_voice_over_video_without_duration():
    job = local_take_job("voice_over_video")
    del job["processing"]["duration_seconds"]

    try:
        validate_storyboard_ffmpeg_job(job)
    except ValueError as exc:
        assert "duration_seconds" in str(exc)
    else:
        raise AssertionError("expected duration_seconds validation failure")


def test_validate_storyboard_local_take_rejects_one_image_audio():
    job = local_take_job("one_image")
    job["inputs"].append({"input_id": 24, "kind": "scene_audio", "role": "voice_over"})

    try:
        validate_storyboard_ffmpeg_job(job)
    except ValueError as exc:
        assert "does not support audio inputs" in str(exc)
    else:
        raise AssertionError("expected audio validation failure")


def segment_normalize_job():
    return {
        "family": "media_processing",
        "media_type": "video",
        "processing_task": "storyboard_ffmpeg_processing",
        "processor_id": "storyboard_ffmpeg",
        "mediaassembly_operation": "segmented_media_segment_normalize",
        "operation_type": "segmented_media_segment_normalize",
        "operation": "segmented_media_segment_normalize",
        "storyboard_type": "video_capture_segmented_media",
        "inputs": [
            {
                "kind": "source_video",
                "input_id": 2353,
                "role": "raw_segment",
                "filename": "software-demo-screen-seg-0005.webm",
                "mime_type": "video/webm",
            }
        ],
        "processing": {
            "operation_type": "segmented_media_segment_normalize",
            "target_width": 1280,
            "target_height": 720,
            "resize_mode": "scale_to_cover_crop",
            "fps": 30,
            "crf": 20,
            "preset": "veryfast",
            "audio_bitrate": "128k",
        },
        "output": {"count": 1, "format": "mp4", "max_duration_seconds": 60},
    }


def test_validate_segment_normalize_accepts_one_source_video_with_target_dimensions():
    processing = validate_storyboard_ffmpeg_job(segment_normalize_job())
    assert processing["operation_type"] == "segmented_media_segment_normalize"
    assert processing["output_width"] == 1280
    assert processing["output_height"] == 720
    assert processing["resize_mode"] == "scale_to_cover_crop"


def test_validate_segment_normalize_rejects_missing_target_dimensions():
    job = segment_normalize_job()
    del job["processing"]["target_width"]
    del job["processing"]["target_height"]

    try:
        validate_storyboard_ffmpeg_job(job)
    except ValueError as exc:
        assert "target_width and target_height" in str(exc)
    else:
        raise AssertionError("expected target dimension validation failure")


def test_validate_segment_normalize_rejects_multiple_video_inputs():
    job = segment_normalize_job()
    job["inputs"].append({"input_id": 2354, "kind": "source_video", "role": "raw_segment", "mime_type": "video/webm"})

    try:
        validate_storyboard_ffmpeg_job(job)
    except ValueError as exc:
        assert "exactly one source_video" in str(exc)
    else:
        raise AssertionError("expected multiple source_video validation failure")
