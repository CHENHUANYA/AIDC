from scripts import browser_e2e_responsive as browser


def test_local_browser_server_does_not_inherit_production_database(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_STORE", "postgresql")
    monkeypatch.setenv("POSTGRES_ENABLED", "true")
    monkeypatch.setenv("DATABASE_URL", "postgresql://production.invalid/live")
    monkeypatch.setattr(browser, "OUT_DIR", tmp_path)
    monkeypatch.setattr(browser, "SCREENSHOT_DIR", tmp_path / "screenshots")
    captured = {}

    def launch(command, **kwargs):
        captured.update(kwargs)
        return "test-process"

    monkeypatch.setattr(browser.subprocess, "Popen", launch)
    assert browser.start_server(8999, preserve_db=False) == "test-process"
    env = captured["env"]
    assert env["DATA_STORE"] == "json"
    assert env["POSTGRES_ENABLED"] == "false"
    assert env["DATABASE_URL"] == ""
    assert env["DB_PATH"] == str(tmp_path / "db")
