from . import configurator
from .utils import screens

from typing import Union
import asyncio
import sys
import os

JIANER_BOT_VERSION = "0.91.1.post3"

# listener = None

screens.play_startup()
screens.play_info(JIANER_BOT_VERSION)


class Client:
    def __init__(self):
        self.records = {}
        self.lis = None
        self.plugin_manager = None
        self._plugin_dispatch_registered = False

    def subscribe(
            self,
            func: callable,
            event: Union[
                "events.GroupMessageEvent",
                "events.PrivateMessageEvent",
                "events.GroupFileUploadEvent",
                "events.GroupAdminEvent",
                "events.GroupMemberDecreaseEvent",
                "events.GroupMemberIncreaseEvent",
                "events.GroupMuteEvent",
                "events.FriendAddEvent",
                "events.GroupRecallEvent",
                "events.FriendRecallEvent",
                "events.NotifyEvent",
                "events.GroupEssenceEvent",
                "events.MessageReactionEvent",
                "events.BotMenuEvent",
                "events.GroupAddInviteEvent",
                "events.HyperListenerStartNotify",
                "events.HyperListenerStopNotify"
            ]
    ) -> None:
        if not self.records.get(event):
            self.records[event] = [func]
        else:
            self.records[event].append(func)

    async def distributor(
            self, message_data: Union["events.Event", "events.HyperNotify"], actions: "Listener.Actions"
    ) -> None:
        if type(message_data) in list(self.records.keys()):
            tasks = []
            for i in self.records[type(message_data)]:
                tasks.append(asyncio.create_task(i(message_data, actions)))
            await asyncio.gather(*tasks)
        else:
            return

    def load_plugins(self, *plugin_folders, **kwargs):
        from . import events
        from .plugins import PluginManager

        if self.plugin_manager is None:
            self.plugin_manager = PluginManager()
        result = self.plugin_manager.load_plugins(*plugin_folders, **kwargs)
        if not self._plugin_dispatch_registered:
            self.subscribe(self.plugin_manager.dispatch, events.GroupMessageEvent)
            self.subscribe(self.plugin_manager.dispatch, events.PrivateMessageEvent)
            self._plugin_dispatch_registered = True
        self.plugin_manager.setup_client(self)
        return result

    def run(self):
        from . import listener
        self.lis = listener
        self.lis.reg(self.distributor)
        if self.records:
            self.lis.run()

    def restart(self) -> None:
        self.lis.stop()
        os.execv(sys.executable, [sys.executable] + sys.argv)
        # os._exit(1)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass
