# JianerCore Plugin Contract

This reference is grounded in the JianerCore source available on 2026-07-22. Re-check the target checkout whenever it is available; source behavior outranks this snapshot and prose documentation.

## Source-of-truth files

- `jianer/plugins/metadata.py`: plugin ID and metadata model.
- `jianer/plugins/manager.py`: static discovery, dependency loading, imports, setup, and dispatch.
- `jianer/plugins/builtin/alconna/__init__.py`: command parsing, injection, unified messages, and targets.
- `jianer/__init__.py`: `Client.load_plugins()` integration.
- `jianer/events.py` and each required adapter's `Actions`: event and protocol behavior.

## Metadata discovery is static

Use a top-level ordinary assignment whose values are literals:

```python
from jianer.plugins import PluginMetadata

__plugin_meta__ = PluginMetadata(
    name="jianerbot-plugin-demo",
    description="A demo plugin",
    usage="Send demo",
    requires={"jianerbot-plugin-alconna"},
)
```

The manager parses the entry file with `ast` before importing it. It recognizes only a direct `PluginMetadata(...)` call assigned with `=` to `__plugin_meta__`. Avoid:

- `__plugin_meta__: PluginMetadata = ...`;
- a variable or expression for `name`, `description`, `usage`, or `requires`;
- an aliased constructor such as `PM(...)`;
- `set()` or `frozenset()` calls in `requires`;
- a fourth positional argument for `requires`.

Plugin IDs match `^jianerbot-plugin-[a-z0-9]+(?:-[a-z0-9]+)*$`. Use the canonical `jianerbot-plugin-alconna` dependency, not its legacy alias.

## Layout and loading

- A single-file plugin is `plugins/name.py` or `.pyw`.
- A directory plugin is `plugins/name/setup.py`; `setup.py` contains the metadata.
- A file or directory beginning with `d_` is disabled.
- `Client.load_plugins("plugins")` creates a `PluginManager`, discovers plugins, subscribes its dispatcher to group/private message events, runs synchronous setup hooks, and returns `LoadResult`.
- Check IDs through `result.loaded`, `result.dependency_order`, or `result.plugin_map`. `result.plugins` contains imported modules, not plugin ID strings.
- A pip-installed module is not auto-discovered through packaging entry points. The host must load a plugin directory/path or supported module explicitly.

The current directory loader executes `setup.py` as an independent module rather than a package. Do not assume relative imports work. Keep the default single-file layout; if helpers are necessary, prove their imports with `PluginManager` in a clean process.

## Alconna command plugins

Declare `requires={"jianerbot-plugin-alconna"}` and import `Command` and `UniMessage` from `jianer.plugins.builtin.alconna`.

No-argument command:

```python
@Command("ping").handle()
async def handle_ping():
    await UniMessage.send(UniMessage.text("pong"))
```

One rest-of-line argument:

```python
@Command("echo <text>").handle()
async def handle_echo(text: str):
    await UniMessage.send(UniMessage.text(text))
```

Do not add `from __future__ import annotations` to this template. With the current injector, a deferred `str` annotation can prevent a `MultiVar` tuple from being normalized into a single string.

The normalizer joins `MultiVar` items with one space. If the user requires exact preservation of repeated whitespace, inject `event` and derive the remainder from `event.msg_str` with a single prefix split; add a regression test containing consecutive spaces.

The string shortcut is reliable only for `command` and `command <one_argument>`. Do not generate `Command("pair <a> <b>")` or mixed literal/placeholder forms; the current coercion does not construct those arguments correctly. Use an explicit `Alconna` object:

```python
from arclet.alconna import Alconna, Args, Option

ban = Alconna(
    "ban",
    Args["user", str],
    Option("--reason", Args["reason", str]),
)

@Command(ban).handle()
async def handle_ban(user: str, reason: str = ""):
    await UniMessage.send(UniMessage.text(f"{user}: {reason}"))
```

Give optional injected parameters Python defaults. If an option is absent, the injector omits that keyword; it does not reliably inject an empty string.

Supported injected names include `event`, `actions`, `user_message`, `result`/`arparma`, and parsed argument names. A command handler is treated as handled unless it returns exactly `False`.

Use `UniMessage`/`Target` for portable text, images, mentions, replies, group targets, and private targets. Test the actual segment behavior on every requested adapter.

## Raw message dispatch

A plugin may omit the Alconna dependency and expose:

```python
from jianer import common, segments

async def on_message(event, actions):
    if getattr(event, "msg_str", "") != "ping":
        return False
    message = common.Message(segments.Text("pong"))
    group_id = getattr(event, "group_id", None)
    if group_id is not None:
        await actions.send(message, group_id=group_id)
    else:
        await actions.send(message, user_id=event.user_id)
    return True
```

Only a response strictly equal to `True` stops later plugin dispatch. Return `False` for a non-match. Prefer `common.Message` over a bare string because not every adapter's low-level `send` accepts strings (notably the current Kritor implementation).

IDs may be strings (for example Feishu open/chat IDs). Do not coerce them to integers unless the verified adapter API requires it.

## Non-message events and setup

`Client.load_plugins()` automatically registers only `GroupMessageEvent` and `PrivateMessageEvent` dispatch. For notice/request events, expose a synchronous hook and subscribe explicitly:

```python
from jianer import events

async def handle_join(event, actions):
    ...

def setup(client, manager):
    client.subscribe(handle_join, events.GroupMemberIncreaseEvent)
```

Do not make `setup` async; the manager does not await it. Verify the event class and translator support in each target adapter before claiming compatibility.

## Dependencies and protocol boundaries

- Put only Jianer plugin IDs in `PluginMetadata.requires`.
- Put PyPI dependencies in the host's dependency file and verify imports plus `pip check` when dependencies change.
- Dependencies load before dependents; missing, failed, circular, invalid, or duplicate IDs prevent loading.
- There is no declared plugin priority field. Do not promise an ordering beyond dependency order.
- Inspect each adapter before using methods beyond portable message sending. Do not infer Feishu, Milky, Kritor, or OneBot semantics from another adapter.

## Minimum proof

1. Compile the entry with the target interpreter.
2. Run static metadata validation.
3. Load through a fresh `PluginManager`; assert the target ID is present and inspect failures/warnings.
4. Dispatch a match and a non-match with fake actions in an isolated test.
5. Cover group/private targets and protocol-specific branches requested by the user.
6. Run focused tests, then the target repository's required broader checks.
