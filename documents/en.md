# JianerCore

JianerCore is the QQ bot framework used by
[SRInternet-Studio/Jianer_QQ_bot](https://github.com/SRInternet-Studio/Jianer_QQ_bot).
It connects to a OneBot v11 implementation, converts incoming payloads into
Python event objects, dispatches events to asynchronous handlers, and exposes
message and group-management actions. Logging is powered by Loguru.

JianerCore is based on HypeR Core. Apart from that codebase origin, JianerCore
is independently maintained and has no other relationship with HypeR Core.

## Installation

JianerCore requires Python 3.9 or newer:

```shell
pip install jianer-bot
```

For development from a source checkout:

```shell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pip install -e .
```

## Basic Usage

The distribution name is `jianer-bot`, while the Python import package is
`jianer`.

```python
from cfgr.manager import Serializers
from jianer import configurator

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

A running OneBot v11 implementation, such as NapCat or Lagrange.OneBot, is
required.
