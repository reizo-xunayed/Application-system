import json

import pytest

from app import config, gmail


class FakeFlow:
    def __init__(self, result=None, error=None):
        self.result, self.error = result, error

    def run_local_server(self, **kwargs):
        assert kwargs["prompt"] == "consent" and kwargs["port"] == 0
        if self.error:
            raise self.error
        return self.result


class FakeCreds:
    def __init__(self, scopes):
        self.granted_scopes = scopes

    def to_json(self):
        return json.dumps({"token": "t"})


@pytest.fixture
def client_file():
    config.GOOGLE_CLIENT_FILE.write_text("{}")


def use_flow(monkeypatch, flow):
    from google_auth_oauthlib import flow as flow_module

    monkeypatch.setattr(flow_module.InstalledAppFlow, "from_client_secrets_file", classmethod(lambda cls, *a, **k: flow))


def test_sign_in_needs_client_file():
    with pytest.raises(gmail.GmailError, match="google_client_secret.json"):
        gmail.sign_in()


def test_sign_in_timeout_is_explained(monkeypatch, client_file):
    from google_auth_oauthlib.flow import WSGITimeoutError

    use_flow(monkeypatch, FakeFlow(error=WSGITimeoutError("Timed out")))
    with pytest.raises(gmail.GmailError, match="timed out"):
        gmail.sign_in()


def test_sign_in_requires_both_permissions(monkeypatch, client_file):
    use_flow(monkeypatch, FakeFlow(result=FakeCreds([gmail.SCOPES[0]])))
    with pytest.raises(gmail.GmailError, match="Both permissions"):
        gmail.sign_in()
    assert not config.GOOGLE_TOKEN_FILE.exists()


def test_sign_in_saves_token(monkeypatch, client_file):
    use_flow(monkeypatch, FakeFlow(result=FakeCreds(gmail.SCOPES)))
    monkeypatch.setattr(gmail, "my_address", lambda: "me@gmail.com")
    assert gmail.sign_in() == "me@gmail.com"
    assert config.GOOGLE_TOKEN_FILE.exists()
    assert oct(config.GOOGLE_TOKEN_FILE.stat().st_mode & 0o777) == "0o600"


class FakeResp:
    status_code = 200

    def json(self):
        return {"id": "m1", "threadId": "t9"}


def test_send_uploads_multipart_with_thread(monkeypatch):
    calls = {}

    class FakeSession:
        def post(self, url, data, headers, timeout):
            calls.update(url=url, data=data, headers=headers)
            return FakeResp()

    monkeypatch.setattr(gmail, "_session", lambda: FakeSession())
    assert gmail.send(b"Subject: Hi\r\n\r\nBody", thread_id="t9") == {"id": "m1", "threadId": "t9"}
    boundary = calls["headers"]["Content-Type"].split("boundary=")[1]
    body = calls["data"].decode()
    assert "uploadType=multipart" in calls["url"]
    assert body.startswith(f"--{boundary}\r\nContent-Type: application/json")
    assert '{"threadId": "t9"}' in body
    assert "Content-Type: message/rfc822\r\n\r\nSubject: Hi\r\n\r\nBody" in body
    assert body.endswith(f"--{boundary}--\r\n")


def test_not_connected_without_token():
    assert not gmail.connected()
    with pytest.raises(gmail.NotConnected):
        gmail.my_address()
