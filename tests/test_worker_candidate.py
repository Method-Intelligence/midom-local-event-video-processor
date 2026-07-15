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
