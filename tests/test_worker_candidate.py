from midom_local_processor.types import WorkerConfig
from midom_local_processor.worker import candidate_incompatibility_reason


def config():
    return WorkerConfig(
        api_base_url="https://midom.example",
        worker_id=10,
        worker_token="secret",
        org_id=1,
        project_id=2,
        paired_user_id=3,
        machine_name="Test",
    )


def test_candidate_compatible_media_processing_job():
    candidate = {
        "job_id": 99,
        "worker_id": 10,
        "org_id": 1,
        "project_id": 2,
        "requested_by_user_id": 3,
        "media_type": "video",
        "family": "media_processing",
        "processing_task": "event_video_processing",
        "processor_id": "event_video_ffmpeg_processor",
    }
    assert candidate_incompatibility_reason(candidate, config()) is None


def test_candidate_rejects_wrong_worker():
    candidate = {
        "job_id": 99,
        "worker_id": 11,
        "org_id": 1,
        "project_id": 2,
        "requested_by_user_id": 3,
        "media_type": "video",
        "family": "media_processing",
    }
    assert "worker_id mismatch" in candidate_incompatibility_reason(candidate, config())


def test_candidate_compatible_storyboard_ffmpeg_job():
    candidate = {
        "job_id": 100,
        "worker_id": 10,
        "org_id": 1,
        "project_id": 2,
        "requested_by_user_id": 3,
        "media_type": "video",
        "family": "media_processing",
        "processing_task": "storyboard_ffmpeg_processing",
        "processor_id": "storyboard_ffmpeg_processor",
        "summary": {
            "operation_type": "replace_video_soundtrack",
            "output_count": 1,
            "output_format": "mp4",
            "video_input_count": 1,
            "audio_input_count": 1,
        },
    }
    assert candidate_incompatibility_reason(candidate, config()) is None


def test_candidate_rejects_storyboard_soundtrack_without_audio():
    candidate = {
        "job_id": 100,
        "worker_id": 10,
        "org_id": 1,
        "project_id": 2,
        "requested_by_user_id": 3,
        "media_type": "video",
        "family": "media_processing",
        "processing_task": "storyboard_ffmpeg_processing",
        "processor_id": "storyboard_ffmpeg_processor",
        "summary": {
            "operation_type": "replace_video_soundtrack",
            "video_input_count": 1,
            "audio_input_count": 0,
        },
    }
    assert "soundtrack audio" in candidate_incompatibility_reason(candidate, config())


def test_candidate_compatible_storyboard_local_one_image():
    candidate = {
        "job_id": 101,
        "worker_id": 10,
        "org_id": 1,
        "project_id": 2,
        "requested_by_user_id": 3,
        "media_type": "video",
        "family": "media_processing",
        "processing_task": "storyboard_ffmpeg_processing",
        "processor_id": "storyboard_ffmpeg_processor",
        "summary": {
            "operation_type": "multicam_card_local_video_take",
            "render_mode": "one_image",
            "image_input_count": 1,
            "start_image_count": 1,
            "end_image_count": 0,
            "audio_input_count": 0,
            "output_count": 1,
            "output_format": "mp4",
        },
    }
    assert candidate_incompatibility_reason(candidate, config()) is None


def test_candidate_compatible_storyboard_local_voice_over_video():
    candidate = {
        "job_id": 102,
        "worker_id": 10,
        "org_id": 1,
        "project_id": 2,
        "requested_by_user_id": 3,
        "media_type": "video",
        "family": "media_processing",
        "processing_task": "storyboard_ffmpeg_processing",
        "processor_id": "storyboard_ffmpeg_processor",
        "summary": {
            "operation_type": "mediastoryboard_card_local_video_take",
            "render_mode": "voice_over_video",
            "video_input_count": 1,
            "audio_input_count": 1,
            "scene_audio_count": 1,
            "duration_seconds": 4.0,
            "output_count": 1,
            "output_format": "mp4",
        },
    }
    assert candidate_incompatibility_reason(candidate, config()) is None


def test_candidate_rejects_storyboard_local_voice_over_video_transition():
    candidate = {
        "job_id": 103,
        "worker_id": 10,
        "org_id": 1,
        "project_id": 2,
        "requested_by_user_id": 3,
        "media_type": "video",
        "family": "media_processing",
        "processing_task": "storyboard_ffmpeg_processing",
        "processor_id": "storyboard_ffmpeg_processor",
        "summary": {
            "operation_type": "multicam_card_local_video_take",
            "render_mode": "voice_over_video",
            "video_input_count": 1,
            "audio_input_count": 1,
            "scene_audio_count": 1,
            "duration_seconds": 4.0,
            "video_transition": "fade",
        },
    }
    assert "video_transition" in candidate_incompatibility_reason(candidate, config())


def test_candidate_compatible_segment_normalize_without_target_dimensions():
    candidate = {
        "job_id": 104,
        "worker_id": 10,
        "org_id": 1,
        "project_id": 2,
        "requested_by_user_id": 3,
        "media_type": "video",
        "family": "media_processing",
        "processing_task": "storyboard_ffmpeg_processing",
        "processor_id": "storyboard_ffmpeg",
        "summary": {
            "operation_type": "segmented_media_segment_normalize",
            "video_input_count": 1,
            "source_video_count": 1,
            "output_count": 1,
            "output_format": "mp4",
        },
    }
    assert candidate_incompatibility_reason(candidate, config()) is None


def test_candidate_rejects_segment_normalize_multiple_videos():
    candidate = {
        "job_id": 105,
        "worker_id": 10,
        "org_id": 1,
        "project_id": 2,
        "requested_by_user_id": 3,
        "media_type": "video",
        "family": "media_processing",
        "processing_task": "storyboard_ffmpeg_processing",
        "processor_id": "storyboard_ffmpeg",
        "summary": {
            "operation_type": "segmented_media_segment_normalize",
            "video_input_count": 2,
            "source_video_count": 2,
        },
    }
    assert "exactly one source_video" in candidate_incompatibility_reason(candidate, config())
