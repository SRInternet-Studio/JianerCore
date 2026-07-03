# JianerCore 插件开发指南

本文面向 JianerCore 当前的新式插件系统。新式插件以 `PluginMetadata` 为入口，支持插件之间声明依赖，并可以使用内置的 `jianer-alconna` 插件完成统一消息接收、命令解析和消息发送。

## 插件系统分层

JianerCore 现在有两套加载入口：

| 入口 | 用途 | 是否进入新派发器 |
| --- | --- | --- |
| `jianer.plugins.PluginManager` / `Client.load_plugins(...)` | 推荐的新式插件系统，支持依赖和内置插件 | 是 |
| `jianer.plugins.load_plugins(...)` / `jianer.plugin_loader.load_plugins(...)` | 兼容旧 Canary 插件，保留 `TRIGGHT_KEYWORD` / `on_message` 契约 | 否 |

新插件必须声明：

```python
from jianer.plugins import PluginMetadata

__plugin_meta__ = PluginMetadata(name="my-plugin")
```

`name` 是插件 ID。两个插件使用同一个 `name` 会加载失败。

## 最小插件

推荐目录结构：

```text
plugins/
└── ping.py
```

`plugins/ping.py`：

```python
from jianer.plugins import PluginMetadata
from jianer.plugins.builtin.alconna import Command, UniMessage

__plugin_meta__ = PluginMetadata(
    name="ping",
    description="Reply pong when receiving ping.",
    requires={"jianer-alconna"},
)


@Command("ping").handle()
async def _():
    await UniMessage.text("pong").send()
```

宿主 bot 使用 `Client.load_plugins("plugins")` 后，这个插件会在收到 `ping` 时回复 `pong`。

## 使用命令参数

字符串命令支持简单的 `<参数名>` 写法：

```python
from jianer.plugins import PluginMetadata
from jianer.plugins.builtin.alconna import Command, UniMessage

__plugin_meta__ = PluginMetadata(name="echo", requires={"jianer-alconna"})


@Command("echo <text>").handle()
async def _(text: str):
    await UniMessage.text(text).send()
```

收到：

```text
echo hello world
```

会回复：

```text
hello world
```

如果需要更完整的 Alconna 能力，可以直接传入 `arclet-alconna` 的 `Alconna` 对象：

```python
from arclet.alconna import Alconna, Args, Option
from jianer.plugins import PluginMetadata
from jianer.plugins.builtin.alconna import Command, UniMessage

__plugin_meta__ = PluginMetadata(name="ban-command", requires={"jianer-alconna"})

ban = Alconna("ban", Args["user", str], Option("--reason", Args["reason", str]))


@Command(ban).handle()
async def _(user: str, reason: str):
    await UniMessage.text(f"ban {user}: {reason}").send()
```

## 访问事件和 actions

处理函数可以声明 `event`、`actions`、`result` 或命令参数名。插件系统会按名称注入：

```python
from jianer.plugins import PluginMetadata
from jianer.plugins.builtin.alconna import Command, UniMessage

__plugin_meta__ = PluginMetadata(name="whoami", requires={"jianer-alconna"})


@Command("whoami").handle()
async def _(event):
    await UniMessage.text(f"user_id={event.user_id}").send()
```

`event` 是 JianerCore 的消息事件对象，常用字段有：

| 字段 | 说明 |
| --- | --- |
| `event.msg_str` | 消息文本 |
| `event.message` | `jianer.common.Message` 消息链 |
| `event.user_id` | 发送者 ID |
| `event.group_id` | 群 ID，私聊事件通常为 `None` |

## 发送消息

`UniMessage` 会转换为 JianerCore 的 `common.Message`：

```python
from jianer.plugins.builtin.alconna import Target, UniMessage

await UniMessage.text("hello").send()
await UniMessage.text("hi ").append(UniMessage.at("10001")).send()
await UniMessage.image("file:///D:/image.png").send()

await UniMessage.text("send to group").send(target=Target.group(123456))
await UniMessage.text("send to user").send(target=Target.private(10001))
```

`send()` 返回 `Receipt`：

```python
receipt = await UniMessage.text("hello").send()
await receipt.reply("again")

if receipt.message_id is not None:
    await receipt.recall()
```

## 声明插件依赖

`requires` 声明 Jianer 插件之间的依赖：

```python
from jianer.plugins import PluginMetadata

__plugin_meta__ = PluginMetadata(
    name="main-plugin",
    requires={"base-plugin", "jianer-alconna"},
)
```

加载规则：

- 依赖会先于当前插件加载。
- 缺失依赖会让当前插件加载失败。
- 依赖导入失败会让依赖它的插件加载失败。
- 循环依赖会加载失败。
- 插件 ID 重复会加载失败。

依赖 ID 使用被依赖插件的 `PluginMetadata.name`。

## 目录插件

单文件插件和目录插件都支持：

```text
plugins/
├── ping.py
└── tools/
    └── setup.py
```

目录插件入口固定为 `setup.py`，并且同样需要声明 `__plugin_meta__`。

## 禁用插件

插件文件或插件目录以 `d_` 开头会被跳过：

```text
plugins/
├── ping.py
└── d_experimental.py
```

## 自定义 dispatch 插件

如果不使用 `jianer-alconna`，插件也可以自己暴露 `dispatch(event, actions)`：

```python
from jianer.plugins import PluginMetadata

__plugin_meta__ = PluginMetadata(name="raw-dispatch")


async def dispatch(event, actions):
    if getattr(event, "msg_str", "") == "ping":
        await actions.send("pong", group_id=event.group_id)
        return True
    return False
```

返回 `True` 表示事件已处理，`PluginManager` 会停止继续派发给后续插件。

## 在 bot 中加载插件

推荐写法：

```python
from jianer import Client

with Client() as client:
    result = client.load_plugins("plugins")
    if result.failed:
        raise RuntimeError(result.failed)
    client.run()
```

手动写法：

```python
from jianer import Client
from jianer.events import GroupMessageEvent, PrivateMessageEvent
from jianer.plugins import PluginManager

manager = PluginManager()
result = manager.load_plugins("plugins")

with Client() as client:
    client.subscribe(manager.dispatch, GroupMessageEvent)
    client.subscribe(manager.dispatch, PrivateMessageEvent)
    manager.setup_client(client)
    client.run()
```

## 旧式插件兼容说明

旧式插件仍可由 `jianer.plugins.load_plugins(...)` 加载：

```python
TRIGGHT_KEYWORD = "hello"
HELP_MESSAGE = "hello help"


async def on_message(*args, **kwargs):
    ...
```

旧式插件保留以下行为：

- 支持 `.py` / `.pyw`。
- 支持 `plugins/name/setup.py`。
- 支持 `d_` 禁用前缀。
- 继续使用拼写错误的 `TRIGGHT_KEYWORD` 保持兼容。

旧式 loader 只负责导入和返回 `LoadResult`，不会自动接入新式 `PluginManager.dispatch(...)`。
