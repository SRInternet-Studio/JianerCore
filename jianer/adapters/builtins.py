from .. import configurator
from . import replace_res, replace_common, replace_listener


def load_onebot():
    from jianer.LecAdapters.OneBotLib import Res as OneBotRes

    replace_res(OneBotRes)

    from jianer.LecAdapters.OneBotLib import Manager as OneBotCommon

    replace_common(OneBotCommon)

    from jianer.LecAdapters import OneBot as OneBotListener

    replace_listener(OneBotListener)


def load_milky():
    from jianer.LecAdapters.MilkyLib import Res as MilkyRes

    replace_res(MilkyRes)

    from jianer.LecAdapters.MilkyLib import Manager as MilkyCommon

    replace_common(MilkyCommon)

    from jianer.LecAdapters import Milky as MilkyListener

    replace_listener(MilkyListener)


def load_feishu():
    from jianer.LecAdapters.FeishuLib import Res as FeishuRes

    replace_res(FeishuRes)

    from jianer.LecAdapters.FeishuLib import Manager as FeishuCommon

    replace_common(FeishuCommon)

    from jianer.LecAdapters import Feishu as FeishuListener

    replace_listener(FeishuListener)


def load_configured(config_name: str = "jianer-bot"):
    config = configurator.BotConfig.get(config_name)
    protocol = str(config.protocol).casefold()
    if protocol == "onebot":
        load_onebot()
    elif protocol == "milky":
        load_milky()
    elif protocol == "feishu":
        load_feishu()
    else:
        raise ValueError(f"Unsupported adapter protocol: {config.protocol}")
