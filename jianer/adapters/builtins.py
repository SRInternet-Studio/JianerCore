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
