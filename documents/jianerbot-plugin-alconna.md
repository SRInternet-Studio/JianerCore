# jianerbot-plugin-alconna API 参考

`jianerbot-plugin-alconna` 是 JianerCore 内置插件，随 `jianer-bot` 发布，无需单独安装。它提供统一的消息接收、命令解析和消息发送能力。

> 插件开发整体流程见 [插件开发指南](plugin-development.md)。本文档侧重 API 签名、参数表格和完整示例。

使用前在插件的 `requires` 中声明依赖即可：

```python
__plugin_meta__ = PluginMetadata(
    name="jianerbot-plugin-my-feature",
    requires={"jianerbot-plugin-alconna"},   # 声明依赖
)
```

---

## 目录

- [快速开始](#快速开始)
- [加载方式](#加载方式)
- [插件命名规则](#插件命名规则)
- [Command — 命令注册](#command--命令注册)
  - [无参数命令](#无参数命令)
  - [带参数命令（尖括号语法）](#带参数命令尖括号语法)
  - [复杂命令（Alconna 对象）](#复杂命令alconna-对象)
  - [on_alconna 别名](#on_alconna-别名)
- [处理函数参数注入](#处理函数参数注入)
  - [event 对象](#event-对象)
  - [actions 对象](#actions-对象)
  - [user_message — 消息链详解](#user_message--消息链详解)
    - [消息链是什么](#消息链是什么)
    - [全部 Segment 类型](#全部-segment-类型)
    - [实战示例](#实战示例)
  - [result / arparma](#result--arparma)
- [UniMessage — 消息构建与发送](#unimessage--消息构建与发送)
  - [创建消息段](#创建消息段)
  - [拼接消息段](#拼接消息段)
  - [发送消息 — send()](#发送消息--send)
  - [Target — 指定发送目标](#target--指定发送目标)
  - [Receipt — 发送回执](#receipt--发送回执)
  - [完整发送示例](#完整发送示例)
- [完整示例](#完整示例)
  - [复读插件](#复读插件)
  - [签到插件](#签到插件)
  - [图片消息处理](#图片消息处理)
  - [封禁命令（Alconna 选项）](#封禁命令alconna-选项)
- [常见问题](#常见问题)

---

## 快速开始

```python
from jianer.plugins import PluginMetadata
from jianer.plugins.builtin.alconna import Command, UniMessage

__plugin_meta__ = PluginMetadata(
    name="jianerbot-plugin-ping",
    requires={"jianerbot-plugin-alconna"},
)

@Command("ping").handle()
async def _():
    await UniMessage.send(UniMessage.text("pong"))
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

---

## 加载方式

**推荐**：让业务插件声明依赖，`PluginManager` 自动处理加载顺序：

```python
__plugin_meta__ = PluginMetadata(
    name="jianerbot-plugin-my-feature",
    requires={"jianerbot-plugin-alconna"},
)
```

**手动**：直接加载：

```python
from jianer.plugins import PluginManager

manager = PluginManager()
manager.load_plugin("jianerbot-plugin-alconna")
```

---

## 插件命名规则

第三方插件 ID 必须使用 `jianerbot-plugin-{name}` 格式：

| 项 | 要求 |
| --- | --- |
| 前缀 | 必须是 `jianerbot-plugin-` |
| 名称字符 | 小写字母、数字和连字符 |
| 允许 | `jianerbot-plugin-aichat`、`jianerbot-plugin-ai-chat` |
| 不允许 | `aichat`、`jianerbot-plugin-AIChat`、`jianer-plugin-aichat` |

文件名可以短一些（如 `plugins/aichat.py`），但 `PluginMetadata.name` 必须使用完整 ID。依赖也用完整 ID：

```python
__plugin_meta__ = PluginMetadata(
    name="jianerbot-plugin-ai-chat",
    requires={"jianerbot-plugin-vector-store", "jianerbot-plugin-alconna"},
)
```

旧 ID `jianer-alconna` 作为过渡别名保留，新插件请统一使用 `jianerbot-plugin-alconna`。

---

## Command — 命令注册

`Command(command)` 返回 `CommandMatcher`，调用 `.handle()` 注册处理函数。

```python
Command(command: str | Alconna) -> CommandMatcher
```

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `command` | `str \| Alconna` | 命令字符串或 Alconna 对象 |

### 无参数命令

```python
@Command("ping").handle()
async def _():
    await UniMessage.send(UniMessage.text("pong"))
```

收到 `ping` 时触发，`ping xxx` 不匹配。

### 带参数命令（尖括号语法）

```python
# <text> 捕获命令名之后的所有剩余文本
@Command("echo <text>").handle()
async def _(text: str):             # 参数名与尖括号内一致
    await UniMessage.send(UniMessage.text(text))
```

| 输入 | text 的值 |
| --- | --- |
| `echo hello` | `"hello"` |
| `echo hello world` | `"hello world"` |

规则：单参数时使用 `MultiVar(str)` 捕获所有剩余文本；多参数时按空格分割。

### 复杂命令（Alconna 对象）

直接传入 `Alconna` 对象获得完整的命令解析能力：

```python
from arclet.alconna import Alconna, Args, Option

# 命令: ban <user> --reason <reason>
ban = Alconna(
    "ban",
    Args["user", str],                           # 必填位置参数
    Option("--reason", Args["reason", str]),      # 可选参数
)

@Command(ban).handle()
async def _(user: str, reason: str):
    await UniMessage.send(UniMessage.text(f"已封禁 {user}，原因：{reason}"))
```

| 输入 | user | reason |
| --- | --- | --- |
| `ban @u --reason spam` | `"@u"` | `"spam"` |
| `ban @u` | `"@u"` | `""` |

### on_alconna 别名

`on_alconna(...)` 与 `Command(...)` 完全等价：

```python
from jianer.plugins.builtin.alconna import Command, on_alconna

@Command("ping").handle()       # 方式 1
@on_alconna("ping").handle()    # 方式 2，等价
```

---

## 处理函数参数注入

处理函数的参数**按名称匹配**，框架自动注入：

| 参数名 | 注入内容 | 类型 | 说明 |
| --- | --- | --- | --- |
| `event` | 消息事件对象 | `GroupMessageEvent \| PrivateMessageEvent` | 包含 user_id、group_id、message 等 |
| `actions` | 适配器 API | 适配器相关 | 底层 send / recall 等接口 |
| `user_message` | 原始消息链 | `common.Message` | 包含所有 segment，可遍历 |
| `result` | Alconna 解析结果 | `Arparma` | 原始解析结果对象 |
| `arparma` | 同 result | `Arparma` | result 的别名 |
| `{命令参数名}` | 解析出的参数值 | Alconna 推导 | 如 text、user、reason 等 |

### event 对象

```python
@Command("whoami").handle()
async def _(event):
    await UniMessage.send(UniMessage.text(f"你的 QQ: {event.user_id}"))
```

| 字段 | 类型 | 群消息 | 私聊 | 说明 |
| --- | --- | --- | --- | --- |
| `event.user_id` | `int` | 有 | 有 | 发送者 QQ |
| `event.group_id` | `int \| None` | 有 | `None` | 群号 |
| `event.message_id` | `str` | 有 | 有 | 消息 ID |
| `event.msg_str` | `str` | 有 | 有 | 纯文本 |
| `event.message` | `common.Message` | 有 | 有 | 消息链 |
| `event.self_id` | `int` | 有 | 有 | 机器人 QQ |
| `event.is_mentioned` | `bool` | 有 | 无 | 是否 @ 了机器人 |

### actions 对象

底层 API，只在需要绕过 UniMessage 时使用：

```python
@Command("send").handle()
async def _(actions, event):
    await actions.send("hello", group_id=event.group_id)
```

日常开发推荐使用 `UniMessage.send()`，更简洁安全。

### user_message — 消息链详解

#### 消息链是什么

一条 QQ 消息由多个消息段（segment）按顺序组成。例如用户发送 `你好 @机器人 看看 [图片]`：

```
Index | Segment   | 内容
  0   | Text      | "你好 "
  1   | At        | qq="12345"
  2   | Text      | " 看看 "
  3   | Image     | file="http://..."
```

`user_message` 就是这个包含所有 segment 的 `common.Message` 对象。

#### 遍历消息链

```python
from jianer.segments import Text, Image, At

@Command("parse").handle()
async def _(user_message):
    for seg in user_message:                     # 逐段遍历
        if isinstance(seg, Text):
            print("文本:", seg.text)
        elif isinstance(seg, Image):
            print("图片:", seg.file)
        elif isinstance(seg, At):
            print("@:", seg.qq)
```

`common.Message` 支持：
- 迭代：`for seg in user_message`
- 索引：`user_message[0]`
- 长度：`len(user_message)`

#### 全部 Segment 类型

以下类型从 `jianer.segments` 导入。

**基础消息段**：

| Segment | `st` | 字段 | 说明 |
| --- | --- | --- | --- |
| `Text` | `"text"` | `.text: str` | 纯文本 |
| `At` | `"at"` | `.qq: str` | @ 用户 |
| `Image` | `"image"` | `.file: str`, `.url: str`, `.summary: str` | 图片 |
| `Reply` | `"reply"` | `.id: str` | 引用回复 |
| `Faces` | `"face"` | `.id: str` | QQ 表情 |
| `Record` | `"record"` | `.file: str`, `.url: str` | 语音 |
| `Video` | `"video"` | `.file: str`, `.url: str` | 视频 |

**扩展消息段**：

| Segment | `st` | 字段 | 说明 |
| --- | --- | --- | --- |
| `Poke` | `"poke"` | `.type: str`, `.id: str` | 戳一戳 |
| `Contact` | `"contact"` | `.type: str`, `.id: str` | 推荐好友/群 |
| `Forward` | `"forward"` | `.id: str` | 转发消息 |
| `Node` | `"node"` | `.user_id: str`, `.nickname: str`, `.content` | 转发节点 |
| `LongMessage` | `"longmsg"` | `.id: str` | 长消息 |
| `Json` | `"json"` | `.data: dict\|list\|str` | JSON 卡片 |
| `MarketFace` | `"mface"` | `.face_id: str`, `.tab_id: str`, `.key: str` | 商城表情 |
| `Music` | `"music"` | `.type: str`, `.url: str`, `.id: str`, `.audio: str`, `.title: str` | 音乐分享 |
| `Dice` | `"dice"` | 无字段 | 骰子 |
| `Rps` | `"rps"` | 无字段 | 猜拳 |

#### 实战示例

**提取 @ 列表**：

```python
from jianer.segments import At

@Command("atme").handle()
async def _(user_message):
    at_list = [seg.qq for seg in user_message if isinstance(seg, At)]
    if at_list:
        await UniMessage.send(UniMessage.text(f"你 @ 了: {', '.join(at_list)}"))
```

**图文混排处理**：

```python
from jianer.segments import Text, Image, At

@Command("analyze").handle()
async def _(user_message):
    images = [seg for seg in user_message if isinstance(seg, Image)]
    texts = [seg.text for seg in user_message if isinstance(seg, Text)]
    if images:
        url = images[0].url or images[0].file
        await UniMessage.send(UniMessage.text(f"收到 {len(images)} 张图, 第一张: {url}"))
```

**user_message 与 text 参数对比**：

| 维度 | `text: str` | `user_message` |
| --- | --- | --- |
| 拿到的内容 | Alconna 解析后的纯文本 | 完整消息链（含所有 segment） |
| 能区分图片/At？ | 不能 | 能 |
| 需要命令匹配？ | 是 | 否（独立于命令） |
| 典型场景 | 简单文字命令 | 混合内容（图+文+@）处理 |

两者可同时使用：

```python
@Command("note <text>").handle()
async def _(text: str, user_message):
    has_image = any(isinstance(seg, Image) for seg in user_message)
    prefix = "[含图] " if has_image else ""
    await UniMessage.send(UniMessage.text(f"{prefix}{text}"))
```

### result / arparma

需要访问 Alconna 原始解析结果时使用：

```python
@Command("echo <text>").handle()
async def _(result, text: str):
    print("匹配到的命令:", result.origin)          # Alconna 对象
    print("解析参数:", text)
```

---

## UniMessage — 消息构建与发送

### 创建消息段

```python
from jianer.plugins.builtin.alconna import UniMessage

# 文本 — 纯文字消息
UniMessage.text("你好")

# @ 用户 — @ QQ 号
UniMessage.at("10001")

# 图片 — 本地/URL/base64
UniMessage.image("file:///D:/images/a.png")
UniMessage.image("https://example.com/img.png")
UniMessage.image("base64://...")

# 回复 — 引用某条消息
UniMessage.reply("42")
```

### 拼接消息段

使用 `+` 构建多段消息（也可用 `.append()`）：

```python
msg = UniMessage.text("hi ") + UniMessage.at("10001")
msg = UniMessage.reply("42") + UniMessage.text("收到")

# 转成 common.Message
chain = msg.to_message()                        # jianer.common.Message 实例
```

### 发送消息 — send()

核心发送方法：

```python
# 单段
await UniMessage.send(UniMessage.text("hello"))

# 多段逗号拼接
await UniMessage.send(
    UniMessage.at("10001"),
    UniMessage.text(" 你好！"),
)

# 纯字符串自动转文本
await UniMessage.send("hello")
```

`send()` 从当前事件自动推断目标（群消息→群号，私聊→用户号）。

#### 完整签名

```python
async def send(
    self: UniMessage | str,         # 【必填】第一个消息段
    *args: UniMessage | str,        # 可选，更多消息段
    target: Target | None = None,   # 可选，发送目标
    actions: Any | None = None,     # 可选，适配器 API
    event: Any | None = None,       # 可选，事件对象
) -> Receipt:
```

| 参数 | 必填 | 类型 | 说明 |
| --- | --- | --- | --- |
| `self` | **是** | `UniMessage \| str` | 第一个消息段，字符串自动转 Text |
| `*args` | 否 | `UniMessage \| str` | 附加消息段，按逗号顺序拼接 |
| `target` | 否 | `Target \| None` | 发送目标，`None` 时从事件推断 |
| `actions` | 否 | `Any \| None` | 适配器 API，`None` 时从上下文获取 |
| `event` | 否 | `Any \| None` | 消息事件，`None` 时从上下文获取 |

### Target — 指定发送目标

```python
from jianer.plugins.builtin.alconna import Target

# 发到群
t = Target.group(123456)

# 发到私聊
t = Target.private(10001)

# 从事件推断
t = Target.from_event(event)
```

| 方法 | 参数 | 说明 |
| --- | --- | --- |
| `Target.group(group_id)` | `group_id: int \| str` | 群聊目标 |
| `Target.private(user_id)` | `user_id: int \| str` | 私聊目标 |
| `Target.from_event(event)` | `event: Any` | 根据 group_id/user_id 自动判断 |

使用：

```python
await UniMessage.send(UniMessage.text("群消息"), target=Target.group(123456))
await UniMessage.send(UniMessage.text("私聊"), target=Target.private(10001))
```

### Receipt — 发送回执

```python
receipt = await UniMessage.send(UniMessage.text("hello"))

# 获取消息 ID
mid = receipt.message_id                       # int | None

# 回复该消息（同一会话）
await receipt.reply("收到回复")

# 撤回（需要适配器支持且 message_id 不为 None）
if receipt.message_id is not None:
    await receipt.recall()
```

`recall()` 依赖适配器的 `actions.del_message(...)`，不支持时抛 `RuntimeError`。

| 属性/方法 | 类型 | 说明 |
| --- | --- | --- |
| `receipt.message_id` | `int \| None` | 消息 ID |
| `receipt.raw` | `Any` | 适配器原始返回值 |
| `receipt.target` | `Target` | 发送目标 |
| `receipt.reply(message)` | `async` | 回复该消息 |
| `receipt.recall()` | `async` | 撤回该消息 |

### 完整发送示例

```python
@Command("welcome <name>").handle()
async def _(name: str, event):

    # 单段文本
    await UniMessage.send(UniMessage.text(f"欢迎 {name}！"))

    # @ + 文本
    await UniMessage.send(
        UniMessage.at(str(event.user_id)),
        UniMessage.text(f" 欢迎你，{name}！"),
    )

    # 图片 + 文本
    await UniMessage.send(
        UniMessage.image("file:///D:/welcome.png"),
        UniMessage.text(f"欢迎 {name}！"),
    )

    # 指定目标群
    await UniMessage.send(
        UniMessage.text(f"{name} 入群了"),
        target=Target.group(123456),
    )
```

---

## 完整示例

### 复读插件

```python
from jianer.plugins import PluginMetadata
from jianer.plugins.builtin.alconna import Command, UniMessage

__plugin_meta__ = PluginMetadata(
    name="jianerbot-plugin-echo",
    description="复读用户输入",
    requires={"jianerbot-plugin-alconna"},
)

@Command("echo <text>").handle()
async def _(text: str):
    await UniMessage.send(UniMessage.text(text))
```

### 签到插件

```python
from jianer.plugins import PluginMetadata
from jianer.plugins.builtin.alconna import Command, UniMessage

__plugin_meta__ = PluginMetadata(
    name="jianerbot-plugin-checkin",
    description="每日签到",
    requires={"jianerbot-plugin-alconna"},
)

@Command("签到").handle()
async def _(event):
    await UniMessage.send(
        UniMessage.at(str(event.user_id)),
        UniMessage.text(" 签到成功！"),
    )
```

### 图片消息处理

```python
from jianer.plugins import PluginMetadata
from jianer.segments import Image, Text, At
from jianer.plugins.builtin.alconna import Command, UniMessage

__plugin_meta__ = PluginMetadata(
    name="jianerbot-plugin-visual",
    description="处理图文混合消息",
    requires={"jianerbot-plugin-alconna"},
)

@Command("analyze").handle()
async def _(user_message):
    """分析消息中每类 segment 的数量"""
    stats = {
        "text": sum(1 for s in user_message if isinstance(s, Text)),
        "image": sum(1 for s in user_message if isinstance(s, Image)),
        "at": sum(1 for s in user_message if isinstance(s, At)),
    }
    result = f"文本段: {stats['text']}, 图片: {stats['image']}, @: {stats['at']}"
    await UniMessage.send(UniMessage.text(result))
```

### 封禁命令（Alconna 选项）

```python
from arclet.alconna import Alconna, Args, Option
from jianer.plugins import PluginMetadata
from jianer.plugins.builtin.alconna import Command, UniMessage

__plugin_meta__ = PluginMetadata(
    name="jianerbot-plugin-admin",
    description="群管理工具",
    requires={"jianerbot-plugin-alconna"},
)

ban_cmd = Alconna(
    "ban",
    Args["user", str],
    Option("--reason", Args["reason", str]),
    Option("--duration", Args["minutes", int]),
)

@Command(ban_cmd).handle()
async def _(user: str, reason: str, minutes: int):
    dur = f" {minutes} 分钟" if minutes else ""
    rsn = f"，原因: {reason}" if reason else ""
    await UniMessage.send(UniMessage.text(f"已封禁 {user}{dur}{rsn}"))
```

---

## 常见问题

### 为什么我的插件加载失败并提示 invalid plugin ID？

`PluginMetadata.name` 必须使用 `jianerbot-plugin-{name}` 格式：

```python
# ✓ 正确
__plugin_meta__ = PluginMetadata(name="jianerbot-plugin-aichat")

# ✗ 错误
__plugin_meta__ = PluginMetadata(name="aichat")
```

### 我是否需要安装 nonebot-plugin-alconna？

不需要。`jianerbot-plugin-alconna` 是 JianerCore 内置插件，命令解析使用的是 `arclet-alconna`。

### 为什么字符串命令功能比较少？

字符串命令是便利用法，只覆盖最常见场景。复杂解析请传入 `arclet-alconna.Alconna` 对象。

### 可以不使用 jianerbot-plugin-alconna 吗？

可以。插件直接暴露 `on_message(event, actions)` 即可自己处理消息，详情见 [插件开发指南 - 自定义 dispatch 插件](plugin-development.md#自定义-dispatch-插件不依赖-alconna)。

### send() 能发送纯字符串吗？

能。`UniMessage.send("hello")` 会自动转为 `UniMessage.text("hello")` 再发送。
