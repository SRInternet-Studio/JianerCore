import pytest

from jianer.adapters import builtins


class _Config:
    def __init__(self, protocol):
        self.protocol = protocol


def test_load_configured_uses_milky_loader(monkeypatch):
    calls = []

    monkeypatch.setattr(builtins.configurator.BotConfig, "get", lambda name: _Config("Milky"))
    monkeypatch.setattr(builtins, "load_milky", lambda: calls.append("milky"))
    monkeypatch.setattr(builtins, "load_onebot", lambda: calls.append("onebot"))

    builtins.load_configured()

    assert calls == ["milky"]


def test_load_configured_uses_onebot_loader(monkeypatch):
    calls = []

    monkeypatch.setattr(builtins.configurator.BotConfig, "get", lambda name: _Config("OneBot"))
    monkeypatch.setattr(builtins, "load_milky", lambda: calls.append("milky"))
    monkeypatch.setattr(builtins, "load_onebot", lambda: calls.append("onebot"))
    monkeypatch.setattr(builtins, "load_feishu", lambda: calls.append("feishu"))

    builtins.load_configured()

    assert calls == ["onebot"]


def test_load_configured_uses_feishu_loader(monkeypatch):
    calls = []

    monkeypatch.setattr(builtins.configurator.BotConfig, "get", lambda name: _Config("Feishu"))
    monkeypatch.setattr(builtins, "load_milky", lambda: calls.append("milky"))
    monkeypatch.setattr(builtins, "load_onebot", lambda: calls.append("onebot"))
    monkeypatch.setattr(builtins, "load_feishu", lambda: calls.append("feishu"))

    builtins.load_configured()

    assert calls == ["feishu"]


def test_load_configured_rejects_unknown_protocol(monkeypatch):
    monkeypatch.setattr(builtins.configurator.BotConfig, "get", lambda name: _Config("Unknown"))

    with pytest.raises(ValueError, match="Unsupported adapter protocol"):
        builtins.load_configured()
