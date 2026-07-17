from midom_local_processor import worker
from midom_local_processor.types import WorkerConfig
from midom_local_processor.worker import LocalProcessorWorker


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


class FakeApi:
    def __init__(self):
        self.disconnected = False

    def disconnect(self):
        self.disconnected = True


def test_quit_stop_keeps_saved_pairing(monkeypatch):
    deleted = []
    monkeypatch.setattr(worker, "delete_config", lambda: deleted.append(True))

    local_worker = LocalProcessorWorker(config())
    local_worker.request_stop()

    assert local_worker.stop_event.is_set()
    assert local_worker._get_shutdown_action() == "quit"
    assert deleted == []


def test_disconnect_stop_removes_saved_pairing(monkeypatch):
    deleted = []
    monkeypatch.setattr(worker, "delete_config", lambda: deleted.append(True))

    local_worker = LocalProcessorWorker(config())
    fake_api = FakeApi()
    local_worker.api = fake_api
    local_worker.request_disconnect()
    local_worker._disconnect_and_delete_config()

    assert local_worker.stop_event.is_set()
    assert local_worker._get_shutdown_action() == "disconnect"
    assert fake_api.disconnected is True
    assert deleted == [True]
