import requests

from midom_local_processor.midom_api import MidomMethodNotAllowedError, WorkerAuthorizationError, raise_for_midom_error


def response(status_code: int, payload: bytes = b'{"message":"Worker token revoked"}'):
    item = requests.Response()
    item.status_code = status_code
    item._content = payload
    item.reason = "Unauthorized"
    return item


def test_authorization_error_tells_user_to_repair():
    try:
        raise_for_midom_error(response(401), "Heartbeat")
    except WorkerAuthorizationError as exc:
        message = str(exc)
        assert "expired, revoked" in message
        assert "fresh Midom pairing code" in message
    else:
        raise AssertionError("expected WorkerAuthorizationError")


def test_method_not_allowed_is_specific_error():
    try:
        raise_for_midom_error(response(405, b"Method Not Allowed"), "Capabilities update")
    except MidomMethodNotAllowedError as exc:
        assert "HTTP 405" in str(exc)
    else:
        raise AssertionError("expected MidomMethodNotAllowedError")
