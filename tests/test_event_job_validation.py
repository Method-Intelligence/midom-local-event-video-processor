from midom_local_processor.validation import validate_event_job


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
