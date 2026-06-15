from . import configurator, events

config = configurator.BotConfig.get("jianer-bot")

__all__ = ["run", "reg", "stop", "Actions", "config"]

from .adapters.listener import *

events.init()
