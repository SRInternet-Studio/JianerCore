import os

from cfgr.manager import Serializers

from jianer import configurator

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
configurator.BotConfig.load_from(os.path.join(_root, "config.json"), Serializers.JSON, "jianer-bot")
