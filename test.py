import time
import os
from jianer import configurator

from cfgr.manager import Serializers  # Maybe I've forgotten sth when coding for ucfgr? IDK.

try:
    configurator.BotConfig.load_from("config.json", Serializers.JSON, "jianer-bot")
except FileNotFoundError:
    configurator.BotConfig.create_and_write("config.json", Serializers.JSON)
    print("没有找到配置文件，已自动创建，请填写后重启")
    exit(-1)

config = configurator.BotConfig.get("jianer-bot")

if True:
    from jianer import hyperogger

    logger = hyperogger.Logger.create("jianer-bot", config.log_level)

    from jianer.adapters import builtins as adp

    adp.load_onebot()

    from jianer import listener, Client
    from jianer.events import *
    from jianer.common import Message
    from jianer.segments import *


async def handler_msg(event: GroupMessageEvent, actions: listener.Actions):
    if str(event.message) == "ping":
        logger.info("有人拍我！")
        res = await actions.send("pong", group_id=event.group_id)
        msg_id = res.data.message_id
        await actions.send(Message(Text("Hello from JianerCore"), Image(file=f"file://{os.path.abspath('./ban.png')}")), group_id=event.group_id)
        time.sleep(3)
        await actions.del_message(msg_id)


with Client() as cli:
    cli.subscribe(handler_msg, GroupMessageEvent)
    cli.run()
