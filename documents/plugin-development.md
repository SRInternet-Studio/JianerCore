# JianerCore 插件开发指南

本文面向 JianerCore 新式插件系统。新式插件以 `PluginMetadata` 为入口，通过内置的 `jianerbot-plugin-alconna` 完成命令匹配、消息接收和消息发送。

> **API 参考**：本文涵盖插件开发全流程。Command、UniMessage、Target、Receipt 等 API 的完整签名和参数详情见 [jianerbot-plugin-alconna API 参考](jianerbot-plugin-alconna.md)。

---

## 快速开始

### 目录结构

```
plugins/
└── ping.py
```

### 最小插件

`plugins/ping.py`：

```python
from jianer.plugins import PluginMetadata
from jianer.plugins.builtin.alconna import Command, UniMessage

# 【必填】插件元信息，name 是插件唯一 ID
__plugin_meta__ = PluginMetadata(
    name="jianerbot-plugin-ping",                    # 必填，插件 ID
    description="收到 ping 时回复 pong",              # 可选，插件描述
    usage="发送 ping 即可",                           # 可选，使用说明
    requires={"jianerbot-plugin-alconna"},            # 可选，依赖的其他插件 ID
)

# 注册命令：收到 "ping" 时触发
@Command("ping").handle()
async def _():
    await UniMessage.send(UniMessage.text("pong"))    # 回复 "pong"
```

宿主 bot 使用 `Client.load_plugins("plugins")` 后，收到 `ping` 即回复 `pong`。

**关键要素**：
1. `__plugin_meta__` — 声明插件身份（name 必填）
2. `@Command("ping").handle()` — 注册命令
3. `UniMessage.send(...)` — 发送回复

---

## PluginMetadata — 插件元信息

每个插件文件必须声明 `__plugin_meta__`：

```python
from jianer.plugins import PluginMetadata

__plugin_meta__ = PluginMetadata(
    name="jianerbot-plugin-xxx",    # 【必填】插件 ID
    description="",                 # 可选，插件描述
    usage="",                       # 可选，使用说明
    requires={},                    # 可选，依赖插件 ID 集合
)
```

| 字段 | 必填 | 类型 | 说明 |
| --- | --- | --- | --- |
| `name` | **是** | `str` | 插件唯一 ID，格式 `jianerbot-plugin-{小写字母与连字符}`。两个插件同名会加载失败 |
| `description` | 否 | `str` | 插件描述信息 |
| `usage` | 否 | `str` | 使用说明 |
| `requires` | 否 | `set[str]` | 依赖的其他插件 ID，依赖会先于当前插件加载 |

**name 格式规则**：
- 必须以 `jianerbot-plugin-` 开头
- 后续为小写字母、数字、连字符：`jianerbot-plugin-aichat`、`jianerbot-plugin-my-tools`
- 内置 Alconna 插件 ID 为 `jianerbot-plugin-alconna`

---

## Command — 命令注册

`@Command(...).handle()` 将处理函数注册为命令处理器。收到匹配的消息时自动触发。

### 签名

```python
Command(command: str | Alconna) -> CommandMatcher
```

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `command` | `str \| Alconna` | 命令字符串（如 `"echo <text>"`）或 Alconna 对象 |

返回 `CommandMatcher`。调用 `.handle()` 注册处理函数：

```python
@Command("ping").handle()           # CommandMatcher 实例
async def _():                      # 注册到该 matcher 的 handlers 中
    ...
```

一个 `CommandMatcher` 可以注册多个处理函数（`.handle()` 可重复调用），匹配时按注册顺序依次执行。

### 无参数命令

命令名完全匹配时触发：

```python
@Command("ping").handle()
async def _():
    await UniMessage.send(UniMessage.text("pong"))
```

收到 `ping` 时回复 `pong`。`ping xxx` 不会匹配。

### 带参数命令（尖括号语法）

在命令名后追加 `<参数名>`，Alconna 自动解析后注入处理函数：

```python
# echo <text>：<text> 捕获命令名之后的所有剩余文本
@Command("echo <text>").handle()
async def _(text: str):             # text 参数名必须与尖括号内一致
    await UniMessage.send(UniMessage.text(text))
```

| 输入 | text 的值 |
| --- | --- |
| `echo hello` | `"hello"` |
| `echo hello world` | `"hello world"` |
| `echo 123 abc` | `"123 abc"` |

**解析规则**：
- `命令名 <参数>`：单参数时使用 `MultiVar(str)`，**捕获所有剩余文本**
- `命令名 <参数1> <参数2>`：多参数时按空格分割

### 复杂命令（Alconna 对象）

需要选项、多参数、可选参数等高级解析时，直接传入 `Alconna` 对象：

```python
from arclet.alconna import Alconna, Args, Option

# 命令结构: ban <user> --reason <reason>
ban = Alconna(
    "ban",                                       # 命令名
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
| `ban @u` | `"@u"` | `""` (Option 参数默认空字符串) |

---

## 处理函数参数注入

处理函数的参数**按名称匹配**，框架自动注入对应值。你需要什么就声明什么，不用的不写。

### 完整参数表

| 参数名 | 注入内容 | 类型 | 何时可用 |
| --- | --- | --- | --- |
| `event` | 当前消息事件对象 | `GroupMessageEvent \| PrivateMessageEvent` | 始终 |
| `actions` | 适配器底层 API | 适配器相关 | 始终 |
| `user_message` | 原始消息链 | `common.Message` | 始终 |
| `result` | Alconna 解析结果 | `Arparma` | 匹配成功时 |
| `arparma` | 同 `result` | `Arparma` | 匹配成功时 |
| `{命令参数名}` | 解析出的参数值 | 由 Alconna 决定 | 匹配成功时 |

### event 对象

```python
@Command("whoami").handle()
async def _(event):                              # 注入完整事件对象
    uid = event.user_id                          # 发送者 QQ 号
    gid = event.group_id                         # 群号，私聊为 None
    mid = event.message_id                       # 消息 ID
    raw = event.msg_str                          # 纯文本
    chain = event.message                        # 消息链
    await UniMessage.send(UniMessage.text(f"你好，你的 QQ 是 {uid}"))
```

**常用字段**：

| 字段 | 类型 | 群消息 | 私聊消息 | 说明 |
| --- | --- | --- | --- | --- |
| `event.user_id` | `int` | 有 | 有 | 发送者 QQ 号 |
| `event.group_id` | `int \| None` | 有 | `None` | 群号 |
| `event.message_id` | `str` | 有 | 有 | 消息唯一 ID |
| `event.msg_str` | `str` | 有 | 有 | 消息纯文本 |
| `event.message` | `common.Message` | 有 | 有 | 消息链 |
| `event.self_id` | `int` | 有 | 有 | 机器人 QQ |
| `event.is_mentioned` | `bool` | 有 | 无 | 是否 @ 了机器人 |

### actions 对象

```python
@Command("send").handle()
async def _(actions, event):                     # actions 是底层 API
    # 等效于 UniMessage.send，但更底层
    await actions.send("hello", group_id=event.group_id)
    await actions.send("hi", user_id=event.user_id)
```

直接使用 `UniMessage.send()` 更推荐，`actions` 仅在需要底层控制时使用。

---

## user_message — 消息链详解

`user_message` 注入的是 `common.Message` 对象，也就是一条消息的**原始消息链**。

### 消息链是什么

一条 QQ 消息由多个**消息段（segment）**按顺序组成。比如用户发送：

```
你好 @机器人 看这张图 [图片]
```

实际是一条消息链，包含 4 个 segment：

```
Index | Segment 类型 | 内容
  0   | Text         | "你好 "
  1   | At           | qq="12345"（机器人）
  2   | Text         | " 看这张图 "
  3   | Image        | file="http://..."
```

### 遍历消息链

```python
from jianer.segments import Text, Image, At

@Command("parse").handle()
async def _(user_message):                       # common.Message 对象
    for seg in user_message:                     # 逐段遍历
        if isinstance(seg, Text):
            print("文本:", seg.text)
        elif isinstance(seg, Image):
            print("图片:", seg.file)
        elif isinstance(seg, At):
            print("@ 了 QQ:", seg.qq)
```

要点：
- `user_message` 可迭代，`for seg in user_message` 按顺序遍历每个 segment
- 用 `isinstance(seg, 类型)` 判断 segment 类型
- 通过 `len(user_message)` 获取段数
- 通过 `user_message[0]` 按索引访问某一段

### 全部 Segment 类型

下述类型从 `jianer.segments` 导入（如 `from jianer.segments import Text, Image, At`）。

**基础消息段**：

| Segment | `st` 标识 | 字段 | 说明 |
| --- | --- | --- | --- |
| `Text` | `"text"` | `.text: str` | 纯文本 |
| `At` | `"at"` | `.qq: str` | @ 某人 |
| `Image` | `"image"` | `.file: str`, `.summary: str`, `.url: str` | 图片 |
| `Reply` | `"reply"` | `.id: str` | 引用回复 |
| `Faces` | `"face"` | `.id: str` | QQ 表情 |
| `Record` | `"record"` | `.file: str`, `.url: str` | 语音 |
| `Video` | `"video"` | `.file: str`, `.url: str` | 视频 |

**扩展消息段**：

| Segment | `st` 标识 | 字段 | 说明 |
| --- | --- | --- | --- |
| `Poke` | `"poke"` | `.type: str`, `.id: str` | 戳一戳 |
| `Contact` | `"contact"` | `.type: str`, `.id: str` | 推荐好友/群 |
| `Forward` | `"forward"` | `.id: str` | 转发消息 |
| `Node` | `"node"` | `.user_id: str`, `.nickname: str`, `.content` | 转发消息节点 |
| `LongMessage` | `"longmsg"` | `.id: str` | 长消息 |
| `Json` | `"json"` | `.data: dict\|list\|str` | JSON 卡片 |
| `MarketFace` | `"mface"` | `.face_id: str`, `.tab_id: str`, `.key: str` | 商城表情 |
| `Music` | `"music"` | `.type: str`, `.url: str`, `.id: str`, `.audio: str`, `.title: str` | 音乐分享 |
| `Dice` | `"dice"` | 无字段 | 骰子 |
| `Rps` | `"rps"` | 无字段 | 猜拳 |

### 实战：图片 + 文本处理

```python
from jianer.segments import Text, Image
from jianer.plugins.builtin.alconna import Command, UniMessage

@Command("ocr").handle()
async def _(user_message, event):
    """识别用户发的图片内容（示例）"""
    for seg in user_message:
        if isinstance(seg, Image):
            # 拿到图片地址，做 OCR 或其他处理
            image_url = seg.url or seg.file
            await UniMessage.send(UniMessage.text(f"收到图片: {image_url}"))
        elif isinstance(seg, Text):
            # 处理文本
            print(f"附带文本: {seg.text}")
```

### 实战：提取 @ 列表

```python
from jianer.segments import At

@Command("atme").handle()
async def _(user_message):
    """列出消息中所有被 @ 的人"""
    at_list = [seg.qq for seg in user_message if isinstance(seg, At)]
    if at_list:
        await UniMessage.send(UniMessage.text(f"你 @ 了: {', '.join(at_list)}"))
    else:
        await UniMessage.send(UniMessage.text("你没有 @ 任何人"))
```

### user_message vs text 参数

| 维度 | `text: str`（命令参数） | `user_message` |
| --- | --- | --- |
| 拿到的内容 | Alconna 解析后的纯文本字符串 | 完整消息链（含所有 segment） |
| 能区分图片/At 吗 | 不能 | 能 |
| 需要命令匹配吗 | 是——<text> 是命令的一部分 | 否——独立于命令参数 |
| 典型场景 | 简单文字命令 | 需要处理混合内容（图+文+@） |

两者可同时使用：

```python
@Command("note <text>").handle()
async def _(text: str, user_message):
    # text: 命令参数，如 "note 备忘内容" 中的 "备忘内容"
    # user_message: 整条消息链，可检查是否附带图片
    has_image = any(isinstance(seg, Image) for seg in user_message)
    if has_image:
        await UniMessage.send(UniMessage.text(f"备忘（含图）: {text}"))
```

---

## UniMessage — 消息构建与发送

`UniMessage` 是统一消息构建器，最终转换为底层 `common.Message` 发出。

### 创建消息段

```python
from jianer.plugins.builtin.alconna import UniMessage

# 文本
UniMessage.text("你好")

# @ 用户
UniMessage.at("10001")                           # QQ 号

# 图片
UniMessage.image("file:///D:/images/a.png")      # 本地文件
UniMessage.image("https://example.com/img.png")  # URL
UniMessage.image("base64://...")                 # base64

# 回复消息
UniMessage.reply("42")                           # 引用的消息 ID
```

### 发送消息

`UniMessage.send()` 是核心发送方法：

```python
# 单段发送
await UniMessage.send(UniMessage.text("hello"))

# 多段用逗号拼接（顺序 = 发送顺序）
await UniMessage.send(
    UniMessage.at("10001"),
    UniMessage.text(" 你好！"),
)

# 纯字符串自动转文本
await UniMessage.send("hello")                   # 等效于 UniMessage.text("hello")
```

#### send() 完整签名

```python
async def send(
    self: UniMessage | str,         # 【必填】第一个消息段，或纯字符串
    *args: UniMessage | str,        # 可选，更多消息段，逗号分隔
    target: Target | None = None,   # 可选，发送目标
    actions: Any | None = None,     # 可选，适配器 API
    event: Any | None = None,       # 可选，事件对象
) -> Receipt:                       # 返回发送回执
```

| 参数 | 必填 | 类型 | 说明 |
| --- | --- | --- | --- |
| `self` | **是** | `UniMessage \| str` | 第一个消息段，字符串自动转 Text |
| `*args` | 否 | `UniMessage \| str` | 附加消息段，按逗号顺序拼接 |
| `target` | 否 | `Target \| None` | 发送目标，`None` 时从当前事件推断 |
| `actions` | 否 | `Any \| None` | 适配器 API，`None` 时从上下文获取 |
| `event` | 否 | `Any \| None` | 消息事件，`None` 时从上下文获取 |

**返回值**：`Receipt`（发送回执），包含 `message_id`、`reply()`、`recall()`。

### Target — 指定发送目标

在命令处理函数外发送消息，或需要发到指定会话时使用：

```python
from jianer.plugins.builtin.alconna import Target

# 发到指定群
T = Target.group(123456)

# 发到指定用户（私聊）
T = Target.private(10001)

# 从事件自动推断（群消息 -> 群号，私聊 -> 用户号）
T = Target.from_event(event)

# 使用
await T.send("hello", actions)                   # 底层调用
await UniMessage.send(UniMessage.text("hi"), target=T)  # 推荐写法

# 类方法签名
Target.group(group_id: int | str) -> Target
Target.private(user_id: int | str) -> Target
Target.from_event(event: Any) -> Target           # 根据 group_id / user_id 判断
```

### Receipt — 发送回执

```python
receipt = await UniMessage.send(UniMessage.text("hello"))

# 获取消息 ID
mid = receipt.message_id                         # int | None

# 回复该消息（发到同一会话）
await receipt.reply("收到回复")

# 撤回（需要适配器支持且 message_id 不为 None）
if receipt.message_id is not None:
    await receipt.recall()
```

**Receipt 接口**：

| 属性/方法 | 类型 | 说明 |
| --- | --- | --- |
| `receipt.message_id` | `int \| None` | 消息 ID，适配器不支持时为 `None` |
| `receipt.raw` | `Any` | 适配器原始返回值 |
| `receipt.target` | `Target` | 发送目标 |
| `receipt.reply(message)` | `async` | 回复该消息 |
| `receipt.recall()` | `async` | 撤回该消息 |

### 完整发送示例

```python
@Command("welcome <name>").handle()
async def _(name: str, event):
    """欢迎新成员"""

    # 方式 1：单行发送
    await UniMessage.send(UniMessage.text(f"欢迎 {name} 加入！"))

    # 方式 2：@ + 文本
    await UniMessage.send(
        UniMessage.at(str(event.user_id)),
        UniMessage.text(f" 欢迎你，{name}！"),
    )

    # 方式 3：图片 + 文本
    await UniMessage.send(
        UniMessage.image("file:///D:/welcome.png"),
        UniMessage.text(f"欢迎 {name}！"),
    )

    # 方式 4：指定目标（发到群 123456）
    await UniMessage.send(
        UniMessage.text(f"{name} 加入了"),
        target=Target.group(123456),
    )
```

---

## 插件依赖

插件之间可以通过 `requires` 声明依赖关系，框架自动处理加载顺序：

```python
__plugin_meta__ = PluginMetadata(
    name="jianerbot-plugin-main",
    requires={
        "jianerbot-plugin-alconna",    # 依赖命令系统
        "jianerbot-plugin-database",   # 依赖数据库
    },
)
```

**加载规则**：
- 依赖插件先于当前插件加载
- 缺失的依赖 → 当前插件加载失败（记录在 `LoadResult.failed`）
- 循环依赖 → 所有参与循环的插件加载失败
- 依赖 ID 使用被依赖插件的 `PluginMetadata.name`
- 依赖本身也会先加载它自己的依赖（递归）

---

## 目录插件

单文件之外也支持目录形式的插件：

```
plugins/
├── ping.py              # 单文件插件
└── tools/               # 目录插件
    └── setup.py         # 入口，必须声明 __plugin_meta__
```

`setup.py` 的写法与单文件插件完全一致。目录下的其他 `.py` 文件不会被自动加载，需在 `setup.py` 中手动 `import`。

---

## 禁用插件

文件名或目录名以 `d_` 开头会被跳过：

```
plugins/
├── ping.py              # 正常加载
├── d_experimental.py    # 跳过
└── d_old_tools/         # 跳过
    └── setup.py
```

---

## 自定义 dispatch 插件（不依赖 alconna）

不依赖 `jianerbot-plugin-alconna` 时，可以直接暴露 `on_message(event, actions)` 函数：

```python
from jianer.plugins import PluginMetadata

__plugin_meta__ = PluginMetadata(name="jianerbot-plugin-raw")


async def on_message(event, actions):
    """PluginManager 自动发现并调用此函数"""
    text = getattr(event, "msg_str", "")
    gid = getattr(event, "group_id", None)

    if text == "ping":
        await actions.send("pong", group_id=gid)
        return True     # 已处理，停止派发

    return False        # 未处理，继续派发给下一个插件
```

**规则**：
- 函数签名必须是 `async def on_message(event, actions)`
- 返回 `True`：事件已处理，`PluginManager` 停止继续派发
- 返回 `False`：未处理，交给下一个插件
- 不需要声明 `requires={"jianerbot-plugin-alconna"}`
- 不能使用 `@Command`、`UniMessage.send()`（它们依赖 alconna）

---

## 在 bot 中加载插件

### 推荐写法

```python
from jianer import Client

with Client() as client:
    result = client.load_plugins("plugins")     # 加载目录下所有插件
    if result.failed:                           # failed 是加载失败的插件名列表
        print("加载失败:", result.failed)
        raise RuntimeError(result.failed)
    client.run()                                # 启动
```

`result` 是 `LoadResult` 对象：

| 属性 | 类型 | 说明 |
| --- | --- | --- |
| `result.plugins` | `list[str]` | 成功加载的插件名列表 |
| `result.failed` | `list[str]` | 加载失败的插件名列表 |
| `result.dependency_order` | `list[str]` | 按拓扑排序的插件加载顺序 |

### 手动写法

需要更精细控制时：

```python
from jianer import Client
from jianer.events import GroupMessageEvent, PrivateMessageEvent
from jianer.plugins import PluginManager

manager = PluginManager()
result = manager.load_plugins("plugins")

with Client() as client:
    # 把 PluginManager 的 dispatch 订阅到消息事件
    client.subscribe(manager.dispatch, GroupMessageEvent)
    client.subscribe(manager.dispatch, PrivateMessageEvent)
    manager.setup_client(client)                # 调用各插件的 setup()
    client.run()
```

---

## 完整示例

### 复读机

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

### 签到

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
    uid = event.user_id
    await UniMessage.send(
        UniMessage.at(str(uid)),
        UniMessage.text(" 签到成功！"),
    )
```

### 图片识别（user_message 实战）

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
async def _(user_message, event):
    """分析用户发送的消息内容"""
    images = []
    texts = []
    at_count = 0

    for seg in user_message:
        if isinstance(seg, Text):
            texts.append(seg.text)
        elif isinstance(seg, Image):
            images.append(seg.url or seg.file)
        elif isinstance(seg, At):
            at_count += 1

    parts = []
    if texts:
        parts.append(f"文本: {''.join(texts)}")
    if images:
        parts.append(f"图片 {len(images)} 张: {', '.join(images)}")
    if at_count:
        parts.append(f"@ {at_count} 人")

    result = "；".join(parts) if parts else "空消息"
    await UniMessage.send(UniMessage.text(f"[分析结果] {result}"))
```

### 封禁命令（Alconna 复杂参数）

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

### 自定义 dispatch（无 alconna 依赖）

```python
from jianer.plugins import PluginMetadata

__plugin_meta__ = PluginMetadata(name="jianerbot-plugin-simple")


async def on_message(event, actions):
    text = getattr(event, "msg_str", "")
    gid = getattr(event, "group_id", None)
    uid = event.user_id

    if text.startswith("say "):
        content = text[4:]
        await actions.send(content, group_id=gid)
        return True

    if text == "whoami":
        await actions.send(f"你的 QQ: {uid}", group_id=gid or user_id=uid)
        return True

    return False
```

---

## 旧式插件兼容

旧式 Canary 插件仍可由 `jianer.plugins.load_plugins(...)` 加载：

```python
TRIGGHT_KEYWORD = "hello"      # 触发关键词（保留拼写）
HELP_MESSAGE = "hello help"    # 帮助信息

async def on_message(*args, **kwargs):
    ...
```

旧式 loader 行为：
- 支持 `.py` / `.pyw`
- 支持 `plugins/name/setup.py`
- 支持 `d_` 禁用前缀
- **不**接入新式 `PluginManager.dispatch()`，两个体系独立运行
