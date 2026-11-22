"""Server mode: pages that change data stop at the staff login; read-only pages stay open."""

from __future__ import annotations

import pytest

from shared import ui


@pytest.mark.parametrize("number", [2, 11, 16, 20])
def test_data_changing_page_requires_login_in_server_mode(app_test, monkeypatch: pytest.MonkeyPatch, number: int) -> None:
    monkeypatch.setenv("PORTFOLIO_MODE", "server")
    monkeypatch.setenv("PORTFOLIO_STAFF_USERNAME", "duty")
    monkeypatch.setenv("PORTFOLIO_STAFF_PASSWORD", "a-long-test-passphrase")
    at = app_test(number).run()
    assert not at.exception
    assert any("staff login required" in str(i.value) for i in at.info)
    assert [t.label for t in at.text_input] == ["Username", "Password"]


def test_read_only_board_stays_open_in_server_mode(app_test, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PORTFOLIO_MODE", "server")
    monkeypatch.setenv("PORTFOLIO_STAFF_USERNAME", "duty")
    monkeypatch.setenv("PORTFOLIO_STAFF_PASSWORD", "a-long-test-passphrase")
    at = app_test(17).run()
    assert not at.exception
    assert not any("staff login required" in str(i.value) for i in at.info)


def test_local_mode_has_no_login(app_test, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PORTFOLIO_MODE", raising=False)
    at = app_test(2).run()
    assert not at.exception
    assert not any("staff login required" in str(i.value) for i in at.info)
    assert 17 in ui.READ_ONLY_PROJECTS
