import json

from powertrade_crawler.credentials import (
    clear_process_credentials,
    get_credential,
    get_project_root,
    read_credentials,
    save_credential,
    set_process_credential,
)


def test_credentials_are_saved_in_one_json_file(tmp_path):
    path = tmp_path / ".auth" / "credentials.json"

    save_credential("gridstatus_api_key", "grid-key", path=path)
    save_credential("elecheck_authorization", "elecheck-token", path=path)
    save_credential("entsoe_security_token", "entsoe-token", path=path)
    save_credential("elexon_api_key", "elexon-key", path=path)

    assert read_credentials(path) == {
        "gridstatus_api_key": "grid-key",
        "elecheck_authorization": "elecheck-token",
        "entsoe_security_token": "entsoe-token",
        "elexon_api_key": "elexon-key",
    }
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "gridstatus_api_key": "grid-key",
        "elecheck_authorization": "elecheck-token",
        "entsoe_security_token": "entsoe-token",
        "elexon_api_key": "elexon-key",
    }


def test_process_credential_temporarily_overrides_file(tmp_path):
    path = tmp_path / "credentials.json"
    save_credential("entsoe_security_token", "stored-token", path=path)
    set_process_credential("entsoe_security_token", "temporary-token")
    try:
        assert get_credential("entsoe_security_token", path=path) == "temporary-token"
        assert (
            get_credential(
                "entsoe_security_token",
                override="command-token",
                path=path,
            )
            == "command-token"
        )
    finally:
        clear_process_credentials()


def test_frozen_application_uses_executable_directory(monkeypatch, tmp_path):
    executable = tmp_path / "PowertradeCrawler.exe"
    monkeypatch.setattr("powertrade_crawler.credentials.sys.frozen", True, raising=False)
    monkeypatch.setattr("powertrade_crawler.credentials.sys.executable", str(executable))

    assert get_project_root() == tmp_path
