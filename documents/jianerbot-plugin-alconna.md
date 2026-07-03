# jianerbot-plugin-alconna 使用文档

`jianerbot-plugin-alconna` 是 JianerCore 内置插件，用来给新式插件提供统一的消息接收、命令解析和消息发送能力。它不是单独安装的 PyPI 包，而是随 `jianer-bot` 一起发布。

第三方插件的 ID 统一使用：

```text
jianerbot-plugin-{name}
```

例如：

```text
jianerbot-plugin-aichat
jianerbot-plugin-ai-chat
```

旧 ID `jianer-alconna` 仍作为过渡加载别名保留；新插件请统一依赖 `jianerbot-plugin-alconna`。

## 快速开始

插件文件可以放在 `plugins/` 下：

```text
plugins/
└── ping.py
```

`plugins/ping.py`：

```python
from jianer.plugins import PluginMetadata
from jianer.plugins.builtin.alconna import Command, UniMessage

__plugin_meta__ = PluginMetadata(
    name="jianerbot-plugin-ping",
    requires={"jianerbot-plugin-alconna"},
)


@Command("ping").handle()
async def _():
    await UniMessage.text("pong").send()
```

宿主 bot：

```python
from jianer import Client

with Client() as client:
    result = client.load_plugins("plugins")
    if result.failed:
        raise RuntimeError(result.failed)
    client.run()
```

## 加载方式

推荐让业务插件声明依赖：

```python
from jianer.plugins import PluginMetadata

__plugin_meta__ = PluginMetadata(
    name="jianerbot-plugin-my-feature",
    requires={"jianerbot-plugin-alconna"},
)
```

`PluginManager` 会先加载 `jianerbot-plugin-alconna`，再加载依赖它的插件。

也可以手动加载：

```python
from jianer.plugins import PluginManager

manager = PluginManager()
manager.load_plugin("jianerbot-plugin-alconna")
```

## 插件命名规则

新式第三方插件必须把 `PluginMetadata.name` 写成 `jianerbot-plugin-{name}`：

```python
from jianer.plugins import PluginMetadata

__plugin_meta__ = PluginMetadata(name="jianerbot-plugin-ai-chat")
```

规则：

| 项 | 要求 |
| --- | --- |
| 前缀 | 必须是 `jianerbot-plugin-` |
| 名称字符 | 小写字母、数字和连字符 |
| 允许示例 | `jianerbot-plugin-aichat`、`jianerbot-plugin-ai-chat` |
| 不允许示例 | `aichat`、`jianerbot-plugin-AIChat`、`jianer-plugin-aichat` |

文件名或目录名可以短一些，例如 `plugins/aichat.py`，但 `PluginMetadata.name` 必须使用完整插件 ID。插件依赖也必须使用完整 ID：

```python
__plugin_meta__ = PluginMetadata(
    name="jianerbot-plugin-ai-chat",
    requires={"jianerbot-plugin-vector-store", "jianerbot-plugin-alconna"},
)
```

## Command 和 on_alconna

`Command(...)` 和 `on_alconna(...)` 等价，都会返回一个 matcher：

```python
from jianer.plugins.builtin.alconna import Command, on_alconna

Command("ping")
on_alconna("ping")
```

注册处理函数：

```python
@Command("ping").handle()
async def _():
    ...
```

### 简单字符串命令

当前字符串命令支持：

```python
@Command("ping").handle()
async def _():
    ...
```

以及一个尖括号参数：

```python
@Command("echo <text>").handle()
async def _(text: str):
    await UniMessage.text(text).send()
```

收到：

```text
echo hello world
```

`text` 会得到：

```text
hello world
```

### 使用真实 Alconna 对象

复杂命令、选项和更完整的解析能力应直接使用 `arclet-alconna`：

```python
from arclet.alconna import Alconna, Args, Option
from jianer.plugins import PluginMetadata
from jianer.plugins.builtin.alconna import Command, UniMessage

__plugin_meta__ = PluginMetadata(
    name="jianerbot-plugin-admin",
    requires={"jianerbot-plugin-alconna"},
)

ban = Alconna("ban", Args["user", str], Option("--reason", Args["reason", str]))


@Command(ban).handle()
async def _(user: str, reason: str):
    await UniMessage.text(f"ban {user}: {reason}").send()
```

收到：

```text
ban @u --reason spam
```

处理函数会得到：

```python
user == "@u"
reason == "spam"
```

## 处理函数参数注入

处理函数可以按名称声明需要的参数：

| 参数名 | 注入内容 |
| --- | --- |
| `event` | 当前 JianerCore 消息事件 |
| `actions` | 当前适配器 actions |
| `result` | Alconna 解析结果 |
| `arparma` | 同 `result` |
| 命令参数名 | 解析出的命令参数或选项参数 |

示例：

```python
@Command("whoami").handle()
async def _(event, actions):
    await actions.send(f"user_id={event.user_id}", group_id=event.group_id)
```

更推荐使用 `UniMessage.send()`：

```python
@Command("whoami").handle()
async def _(event):
    await UniMessage.text(f"user_id={event.user_id}").send()
```

## UniMessage

`UniMessage` 是统一消息链，最后会转换为 JianerCore 的 `common.Message`。

### 创建文本

```python
msg = UniMessage.text("hello")
```

### 图片

```python
msg = UniMessage.image("file:///D:/images/a.png")
```

### @ 用户

```python
msg = UniMessage.text("hi ").append(UniMessage.at("10001"))
```

### 回复消息

```python
msg = UniMessage.reply("42").append("received")
```

### 转成 common.Message

```python
message = UniMessage.text("hello").to_message()
```

### 发送到当前会话

在命令处理函数内可以直接发送：

```python
await UniMessage.text("hello").send()
```

`send()` 会从当前事件自动推断目标。

## Target

`Target` 用于显式指定发送目标。

```python
from jianer.plugins.builtin.alconna import Target, UniMessage

await UniMessage.text("group").send(target=Target.group(123456))
await UniMessage.text("private").send(target=Target.private(10001))
```

也可以从事件推断：

```python
target = Target.from_event(event)
await target.send("hello", actions)
```

## Receipt

`UniMessage.send()` 返回 `Receipt`：

```python
receipt = await UniMessage.text("hello").send()
```

可用能力：

```python
message_id = receipt.message_id
await receipt.reply("again")

if receipt.message_id is not None:
    await receipt.recall()
```

`recall()` 依赖当前适配器的 `actions.del_message(...)`。如果适配器不支持撤回，或者返回值没有 `message_id`，会抛出 `RuntimeError`。

## 完整示例：复读插件

```python
from jianer.plugins import PluginMetadata
from jianer.plugins.builtin.alconna import Command, UniMessage

__plugin_meta__ = PluginMetadata(
    name="jianerbot-plugin-echo",
    description="Echo command plugin.",
    requires={"jianerbot-plugin-alconna"},
)


@Command("echo <text>").handle()
async def _(text: str):
    await UniMessage.text(text).send()
```

## 完整示例：带选项的管理命令

```python
from arclet.alconna import Alconna, Args, Option

from jianer.plugins import PluginMetadata
from jianer.plugins.builtin.alconna import Command, UniMessage

__plugin_meta__ = PluginMetadata(
    name="jianerbot-plugin-admin-tools",
    requires={"jianerbot-plugin-alconna"},
)

mute = Alconna("mute", Args["user", str], Option("--duration", Args["seconds", int]))


@Command(mute).handle()
async def _(user: str, seconds: int, event):
    await UniMessage.text(
        f"group={event.group_id}, mute {user} for {seconds}s"
    ).send()
```

## 常见问题

### 为什么我的插件加载失败并提示 invalid plugin ID？

新式第三方插件必须使用 `jianerbot-plugin-{name}` 作为 `PluginMetadata.name`：

```python
__plugin_meta__ = PluginMetadata(name="jianerbot-plugin-aichat")
```

不要写：

```python
__plugin_meta__ = PluginMetadata(name="aichat")
```

### 我是否需要安装 nonebot-plugin-alconna？

不需要。`jianerbot-plugin-alconna` 参考了 `nonebot-plugin-alconna` 的统一收发思路，但它是 JianerCore 内置插件。命令解析使用的是 `arclet-alconna`。

### 为什么字符串命令能力比较少？

字符串命令是便利用法，只覆盖最常见的命令和单参数场景。复杂解析请直接传入 `arclet-alconna.Alconna` 对象。

### 可以不使用 jianerbot-plugin-alconna 吗？

可以。插件可以直接实现 `dispatch(event, actions)`，但就需要自己处理命令匹配、目标推断和消息发送。
