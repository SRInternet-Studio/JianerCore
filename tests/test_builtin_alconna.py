"""Tests for the built-in jianer-alconna plugin."""

import asyncio
import textwrap
from pathlib import Path
from types import SimpleNamespace

from arclet.alconna import Alconna, Args, Option

from jianer import Client
from jianer import common, events, segments
from jianer.plugins import PluginManager
from jianer.plugins.builtin import alconna
from jianer.plugins.builtin.alconna import Command, Target, UniMessage


class FakeActions:
    def __init__(self):
        self.sent = []
        self.deleted = []

    async def send(self, message, group_id=None, user_id=None):
        self.sent.append((message, group_id, user_id))
        return SimpleNamespace(data={"message_id": len(self.sent)})

    async def del_message(self, message_id):
        self.deleted.append(message_id)


def _write(path: Path, body: str) -> None:
    path.write_text(textwrap.dedent(body), encoding="utf-8")


def setup_function():
    alconna._clear_matchers()


def test_load_builtin_alconna_plugin():
    manager = PluginManager()

    plugin = manager.load_plugin("jianer-alconna")

    assert plugin is not None
    assert plugin.name == "jianer-alconna"
    assert manager.dependency_order == ["jianer-alconna"]


def test_dependency_auto_loads_builtin_alconna(tmp_path):
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    _write(
        plugins / "echo.py",
        """
        from jianer.plugins import PluginMetadata
        from jianer.plugins.builtin.alconna import Command
        __plugin_meta__ = PluginMetadata(name="echo", requires={"jianer-alconna"})

        @Command("echo <text>").handle()
        async def _(text: str):
            return True
        """,
    )

    result = PluginManager().load_plugins(plugins)

    assert result.failed == []
    assert result.dependency_order == ["jianer-alconna", "echo"]


def test_unimessage_to_common_message():
    message = (
        UniMessage.text("hi")
        .append(UniMessage.at("10001"))
        .append(UniMessage.reply("42"))
    ).to_message()

    assert isinstance(message, common.Message)
    assert isinstance(message[0], segments.Text)
    assert isinstance(message[1], segments.At)
    assert isinstance(message[2], segments.Reply)
    assert message[0].text == "hi"
    assert message[1].qq == "10001"
    assert message[2].id == "42"


def test_target_from_event_group_and_private():
    group_target = Target.from_event(SimpleNamespace(group_id=123, user_id=456))
    private_target = Target.from_event(SimpleNamespace(group_id=None, user_id=456))

    assert group_target == Target.group(123)
    assert private_target == Target.private(456)


def test_unimessage_send_returns_receipt_and_reply():
    actions = FakeActions()
    event = SimpleNamespace(group_id=123, user_id=456)

    receipt = asyncio.run(UniMessage.text("hello").send(actions=actions, event=event))
    reply = asyncio.run(receipt.reply("again"))
    asyncio.run(receipt.recall())

    assert receipt.message_id == 1
    assert reply.message_id == 2
    assert actions.deleted == [1]
    assert actions.sent[0][1:] == (123, None)
    assert actions.sent[0][0][0].text == "hello"
    assert actions.sent[1][0][0].text == "again"


def test_command_dispatch_with_alconna_options():
    actions = FakeActions()
    event = SimpleNamespace(msg_str="ban @u --reason spam", group_id=1, user_id=2)
    seen = {}

    command = Alconna("ban", Args["user", str], Option("--reason", Args["reason", str]))

    @Command(command).handle()
    async def _(user: str, reason: str):
        seen["user"] = user
        seen["reason"] = reason
        await UniMessage.text(f"{user}:{reason}").send()

    handled = asyncio.run(alconna.dispatch(event, actions))

    assert handled is True
    assert seen == {"user": "@u", "reason": "spam"}
    assert actions.sent[0][0][0].text == "@u:spam"


def test_command_does_not_dispatch_when_unmatched():
    called = False

    @Command("echo <text>").handle()
    async def _(text: str):
        nonlocal called
        called = True

    handled = asyncio.run(
        alconna.dispatch(SimpleNamespace(msg_str="other hi", group_id=1), FakeActions())
    )

    assert handled is False
    assert called is False


def test_client_load_plugins_registers_dispatchers(tmp_path):
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    _write(
        plugins / "echo.py",
        """
        from jianer.plugins import PluginMetadata
        from jianer.plugins.builtin.alconna import Command
        __plugin_meta__ = PluginMetadata(name="echo", requires={"jianer-alconna"})

        @Command("echo <text>").handle()
        async def _(text: str):
            return True
        """,
    )
    client = Client()

    result = client.load_plugins(plugins)

    assert result.failed == []
    assert events.GroupMessageEvent in client.records
    assert events.PrivateMessageEvent in client.records
    assert client.plugin_manager is not None


def test_manual_plugin_manager_dispatch():
    actions = FakeActions()
    event = SimpleNamespace(msg_str="echo hi", group_id=1, user_id=2)

    @Command("echo <text>").handle()
    async def _(text: str):
        await UniMessage.text(text).send()

    manager = PluginManager()
    manager.load_plugin("jianer-alconna")
    handled = asyncio.run(manager.dispatch(event, actions))

    assert handled is True
    assert actions.sent[0][0][0].text == "hi"
