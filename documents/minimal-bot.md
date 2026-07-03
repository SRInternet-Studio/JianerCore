# 最小 JianerCore Bot 指南

本文给出两种最小 bot 写法：

- 单文件事件订阅版：文件最少，适合快速验证连接。
- 插件版：多一个 `plugins/` 目录，但后续功能都可以拆成插件。

JianerCore 的安装包名是 `jianer-bot`，Python 导入包名是 `jianer`。

## 安装

在空目录中创建虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install jianer-bot
```

如果是在 JianerCore 仓库内开发：

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
```

## 准备 OneBot 实现

JianerCore 本身不登录 QQ。你需要先启动一个支持 OneBot v11 的实现，例如 NapCat 或 Lagrange.OneBot。

下面示例假设 OneBot 服务监听：

```text
host = 127.0.0.1
port = 5004
mode = FWS
```

`FWS` 表示 JianerCore 作为正向 WebSocket 客户端连接 OneBot 服务。

## 最小 config.json

在 bot 运行目录创建 `config.json`：

```json
{
  "protocol": "OneBot",
  "owner": [],
  "black_list": [],
  "silents": [],
  "connection": {
    "mode": "FWS",
    "ob_auto_startup": false,
    "ob_exec": "",
    "ob_startup_path": "",
    "ob_log_output": false,
    "host": "127.0.0.1",
    "port": 5004,
    "retries": 5,
    "token": "",
    "auth": ""
  },
  "log_level": "INFO",
  "log_use_nf": false,
  "uin": 0,
  "max_workers": 25,
  "others": {}
}
```

如果你的 OneBot 服务设置了访问令牌，把它填入 `connection.token` 或按对应适配器配置要求填写。

## 方案一：最小单文件 bot

目录结构：

```text
my-bot/
├── bot.py
└── config.json
```

`bot.py`：

```python
from cfgr.manager import Serializers

from jianer import Client, configurator

configurator.BotConfig.load_from(
    "config.json",
    Serializers.JSON,
    "jianer-bot",
)

from jianer.adapters import builtins as adapters
from jianer.events import GroupMessageEvent, PrivateMessageEvent

adapters.load_configured()


async def ping(event, actions):
    if event.msg_str.strip() != "ping":
        return

    if event.group_id is not None:
        await actions.send("pong", group_id=event.group_id)
    else:
        await actions.send("pong", user_id=event.user_id)


with Client() as client:
    client.subscribe(ping, GroupMessageEvent)
    client.subscribe(ping, PrivateMessageEvent)
    client.run()
```

启动：

```powershell
.\.venv\Scripts\python.exe .\bot.py
```

向 bot 发送 `ping`，应该收到 `pong`。

## 方案二：最小插件版 bot

目录结构：

```text
my-bot/
├── bot.py
├── config.json
└── plugins/
    └── ping.py
```

`bot.py`：

```python
from cfgr.manager import Serializers

from jianer import Client, configurator

configurator.BotConfig.load_from(
    "config.json",
    Serializers.JSON,
    "jianer-bot",
)

from jianer.adapters import builtins as adapters

adapters.load_configured()


with Client() as client:
    result = client.load_plugins("plugins")
    if result.failed:
        raise RuntimeError(result.failed)
    client.run()
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

启动：

```powershell
.\.venv\Scripts\python.exe .\bot.py
```

## 更小的依赖文件

如果你要把 bot 项目提交到仓库，可以只放一个 `requirements.txt`：

```text
jianer-bot
```

然后部署时执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 常见问题

### 启动后没有响应

先确认 OneBot 实现已经运行，并且 `config.json` 中的 `host`、`port`、`mode` 和 OneBot 服务一致。

### 插件没有加载

检查 `client.load_plugins("plugins")` 的返回值：

```python
result = client.load_plugins("plugins")
print("loaded:", result.loaded)
print("failed:", result.failed)
print("warnings:", result.warnings)
```

新式插件必须声明 `__plugin_meta__ = PluginMetadata(...)`。旧式 `TRIGGHT_KEYWORD` 插件不会进入新式派发器。

### 私聊发送失败

确认事件确实是私聊事件。群消息通常有 `event.group_id`，私聊消息通常只有 `event.user_id`。

### 想减少文件数量

使用“最小单文件 bot”方案，只需要 `bot.py` 和 `config.json`。如果后续功能变多，再迁移到插件版。
