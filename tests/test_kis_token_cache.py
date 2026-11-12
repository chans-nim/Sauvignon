from src.clients.kis_client import KISClient, KISConfig


def test_runner_token_cache_is_reused_across_client_processes(monkeypatch, tmp_path):
    cache = tmp_path / "kis-token.json"
    monkeypatch.setenv("KIS_TOKEN_CACHE_FILE", str(cache))
    calls = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"access_token": "runner-token", "expires_in": 3600}

    def post(url, json, timeout):
        calls.append((url, json["appkey"]))
        return Response()

    monkeypatch.setattr("src.clients.kis_client.requests.post", post)
    config = KISConfig(app_key="test-key", app_secret="test-secret", base_url="https://example.test")

    assert KISClient(config).issue_token() == "runner-token"
    assert cache.is_file()
    assert KISClient(config).issue_token() == "runner-token"
    assert len(calls) == 1


def test_runner_token_cache_is_not_shared_between_app_keys(monkeypatch, tmp_path):
    cache = tmp_path / "kis-token.json"
    monkeypatch.setenv("KIS_TOKEN_CACHE_FILE", str(cache))
    issued = iter(("first-token", "second-token"))

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"access_token": next(issued), "expires_in": 3600}

    monkeypatch.setattr("src.clients.kis_client.requests.post", lambda *args, **kwargs: Response())
    first = KISConfig(app_key="first-key", app_secret="secret", base_url="https://example.test")
    second = KISConfig(app_key="second-key", app_secret="secret", base_url="https://example.test")

    assert KISClient(first).issue_token() == "first-token"
    assert KISClient(second).issue_token() == "second-token"
