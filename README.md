<div align="center">
<h1>JianerCore</h1>
</div>

<p align="center">面向 Jianer_QQ_bot 的可扩展 QQ 机器人框架</p>

<div align="center">
<img src="https://img.shields.io/badge/OneBot-11-black" alt="OneBot 11">
<img src="https://img.shields.io/static/v1?label=LICENSE&message=GPL-3.0&color=lightgrey" alt="GPL-3.0">
</div>

## 项目介绍

JianerCore 是 [SRInternet-Studio/Jianer_QQ_bot](https://github.com/SRInternet-Studio/Jianer_QQ_bot) 使用的 QQ 机器人框架，负责连接 OneBot 实现、接收和分发事件，并向机器人业务代码提供消息发送及群管理等接口。

JianerCore 基于 HypeR Core 开发。除代码基础外，本项目是独立维护的项目，与 HypeR Core 没有其他联系。

JianerCore 本身不负责登录 QQ。运行机器人时，还需要使用支持 OneBot v11 的实现，例如 NapCat 或 Lagrange.OneBot。

## 主要功能

- 通过正向 WebSocket 或 HTTP 连接 OneBot v11 实现
- 订阅并异步处理群消息、私聊消息、通知和请求事件
- 使用 Loguru 提供分级、彩色和异常日志
- 发送、回复和撤回消息
- 支持文字、图片、语音、视频、At、回复、转发和 JSON 等消息段
- 提供禁言、踢出群成员、设置精华消息和群头衔等操作
- 使用适配器结构隔离协议实现与机器人业务逻辑
- 支持机器人主人、黑名单和静默名单配置

## 环境要求

- Python 3.9 或更高版本
- 一个可用的 OneBot v11 实现

本项目当前使用 Python 3.12.7 进行开发。首次配置本地开发环境：

```powershell
D:\Python3127\python.exe -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pip install -e .
```

发行包名为 `jianer-bot`：

```shell
pip install jianer-bot
```

## 快速开始

`jianer-bot` 是安装和发布时使用的发行包名，Python 导入包名为 `jianer`。下面的示例会在收到群消息 `ping` 时回复 `pong`：

```python
from jianer import configurator
from cfgr.manager import Serializers

configurator.BotConfig.load_from(
    "config.json",
    Serializers.JSON,
    "jianer-bot",
)

from jianer.adapters import builtins as adapters

adapters.load_onebot()

from jianer import Client
from jianer.events import GroupMessageEvent


async def handle_group_message(event, actions):
    if str(event.message) == "ping":
        await actions.send("pong", group_id=event.group_id)


with Client() as client:
    client.subscribe(handle_group_message, GroupMessageEvent)
    client.run()
```

加载配置后再加载 OneBot 适配器和事件模块。完整示例可查看 [`test.py`](./test.py)。

## 配置

在项目运行目录创建 `config.json`：

```json
{
  "protocol": "OneBot",
  "owner": [],
  "black_list": [],
  "silents": [],
  "connection": {
    "mode": "FWS",
    "ob_auto_startup": false,
    "ob_exec": "./Lagrange.OneBot/Lagrange.OneBot",
    "ob_startup_path": "./Lagrange.OneBot/",
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

主要配置项：

- `owner`：机器人所有者的 QQ 号列表
- `black_list`：需要标记为已屏蔽的用户或群列表
- `silents`：需要静默处理的用户或群列表
- `connection.mode`：连接模式，`FWS` 表示正向 WebSocket，`HTTPC` 表示 HTTP
- `connection.host`：OneBot 服务地址
- `connection.port`：OneBot 服务端口
- `connection.retries`：连接失败后的最大重试次数
- `connection.ob_auto_startup`：是否由框架启动 OneBot 实现
- `log_level`：日志等级

## 项目结构

```text
jianer/
├── adapters/       适配器接口与加载逻辑
├── LecAdapters/    OneBot、Milky 和 Kritor 适配代码
├── utils/          通用工具
├── events.py       事件定义与转换
├── listener.py     监听器入口
├── network.py      WebSocket 和 HTTP 连接
└── segments.py     消息段定义
```

当前主要使用 OneBot v11 适配器。Milky 和 Kritor 相关代码仍处于开发阶段，部分功能尚未实现。

## 许可证

本项目采用 [GPL-3.0](./LICENSE) 许可证。
