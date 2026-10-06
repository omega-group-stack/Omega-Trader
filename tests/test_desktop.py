"""Desktop launcher.

The packaged app runs on machines with no terminal and no Python, so the
failure modes that matter here are the boring ones: an unwritable install
directory, a port already taken, a dashboard accidentally exposed to the
network.
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from omega import desktop


# --------------------------------------------------------------------------- #
# Where things get written
# --------------------------------------------------------------------------- #
def test_app_home_honours_the_override(monkeypatch, tmp_path):
    monkeypatch.setenv("OMEGA_HOME", str(tmp_path / "custom"))
    assert desktop.app_home() == tmp_path / "custom"


def test_app_home_is_under_localappdata_on_windows(monkeypatch, tmp_path):
    monkeypatch.delenv("OMEGA_HOME", raising=False)
    monkeypatch.setattr(desktop.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))

    home = desktop.app_home()

    assert home == tmp_path / "AppData" / "Local" / "OmegaTrader"


def test_app_home_is_never_the_install_directory(monkeypatch, tmp_path):
    """Program Files is read-only for a standard user.

    Anything the app writes at runtime has to live in the user profile, or
    the first trade journal write fails on a normal Windows account.
    """
    monkeypatch.delenv("OMEGA_HOME", raising=False)
    monkeypatch.setattr(desktop.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\someone\AppData\Local")

    assert "Program Files" not in str(desktop.app_home())


def test_ensure_home_creates_the_tree_and_a_starter_config(monkeypatch, tmp_path):
    monkeypatch.setenv("OMEGA_HOME", str(tmp_path / "home"))

    home = desktop.ensure_home()

    assert (home / "config.yaml").is_file()
    for sub in ("logs", "data", "reports", "runtime"):
        assert (home / sub).is_dir(), sub


def test_ensure_home_does_not_overwrite_existing_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("OMEGA_HOME", str(tmp_path / "home"))
    home = desktop.ensure_home()
    (home / "config.yaml").write_text("# mine\n", encoding="utf-8")

    desktop.ensure_home()

    assert (home / "config.yaml").read_text(encoding="utf-8") == "# mine\n"


def test_bundled_config_template_is_found_in_the_source_tree():
    assert desktop.bundled_file("config/config.yaml") is not None


def test_bundled_file_returns_none_when_missing():
    assert desktop.bundled_file("config/not-a-real-file.yaml") is None


# --------------------------------------------------------------------------- #
# Ports
# --------------------------------------------------------------------------- #
def test_free_port_returns_something_bindable():
    """Bind the probe exactly as the real server will.

    Hand-rolling the socket options here would make the test disagree with
    production on one platform or the other -- which is the whole subtlety
    the helper exists to absorb.
    """
    port = desktop.free_port()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        desktop._probe_bind(probe)
        probe.bind((desktop.HOST, port))


def test_free_port_steps_over_a_port_already_in_use():
    """Users relaunch without closing the old window. That must still work.

    This is the case that caught a real Windows bug: SO_REUSEADDR there
    permits binding over a live listener, so a probe that set it reported
    the busy port as free and uvicorn then failed to start.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as taken:
        desktop._probe_bind(taken)
        taken.bind((desktop.HOST, 0))
        busy = taken.getsockname()[1]
        taken.listen(1)

        assert desktop.free_port(preferred=busy) != busy


def test_the_port_probe_never_steals_a_live_listener():
    """Guards the platform-specific socket options directly.

    If someone "simplifies" _probe_bind to an unconditional SO_REUSEADDR,
    this fails on Windows -- where that flag means the opposite of what it
    means everywhere else.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as live:
        desktop._probe_bind(live)
        live.bind((desktop.HOST, 0))
        port = live.getsockname()[1]
        live.listen(1)

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as intruder:
            desktop._probe_bind(intruder)
            with pytest.raises(OSError):
                intruder.bind((desktop.HOST, port))


def test_free_port_gives_up_loudly_rather_than_returning_garbage():
    with pytest.raises(RuntimeError):
        desktop.free_port(preferred=80, attempts=0)


# --------------------------------------------------------------------------- #
# Configuration forced by the desktop shell
# --------------------------------------------------------------------------- #
def test_desktop_config_is_loopback_only_and_authenticated(tmp_path):
    cfg = desktop.build_config(tmp_path, port=9123, token="s3cret", mode="paper")

    assert cfg.dashboard.host == "127.0.0.1"
    assert cfg.dashboard.port == 9123
    assert cfg.dashboard.auth_token == "s3cret"
    assert cfg.execution.mode == "paper"


def test_desktop_config_redirects_writes_into_the_home_folder(tmp_path):
    cfg = desktop.build_config(tmp_path, port=9123, token="t", mode="paper")

    assert cfg.logging.to_file is True
    assert Path(cfg.logging.dir) == tmp_path / "logs"
    assert Path(cfg.data.csv_dir) == tmp_path / "data"
    assert Path(cfg.storage.path).parent == tmp_path / "runtime"


def test_a_user_who_set_their_own_paths_keeps_them(tmp_path):
    """Only the defaults get relocated; explicit choices are respected."""
    from omega.config import AppConfig

    config_path = tmp_path / "config.yaml"
    cfg = AppConfig()
    cfg.data.csv_dir = r"D:\market-data"
    cfg.dump(config_path)

    built = desktop.build_config(tmp_path, port=9123, token="t", mode="paper")

    assert built.data.csv_dir == r"D:\market-data"


def test_default_mode_is_paper_not_live():
    """A first run must not be able to touch a real account."""
    import inspect

    signature = inspect.signature(desktop.run)
    assert signature.parameters["mode"].default == "paper"
