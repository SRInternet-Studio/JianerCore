import sys
import traceback
import typing

from loguru import logger as loguru_logger

from . import configurator


class Levels:
    TRACE = "TRACE"
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"

    level_names = {
        TRACE,
        DEBUG,
        INFO,
        WARNING,
        ERROR,
        CRITICAL,
    }

    level_nums = {
        TRACE: 5,
        DEBUG: 10,
        INFO: 20,
        WARNING: 30,
        ERROR: 40,
        CRITICAL: 50,
    }


levels = Levels()

_config = configurator.BotConfig.get("jianer-bot")


def _configure_loguru() -> None:
    try:
        loguru_logger.remove(0)
    except ValueError:
        pass

    loguru_logger.add(
        sys.stderr,
        level=levels.TRACE,
        backtrace=True,
        diagnose=False,
        enqueue=False,
    )


_configure_loguru()


class Logger:
    running_loggers = {}

    def __init__(self, level: str | None = None):
        self.log_level = levels.INFO
        self._logger = loguru_logger
        self.set_level(level or (_config.log_level if _config else levels.INFO))

    @classmethod
    def create(cls, key: str, level: str):
        instance = cls(level).bind(component=key)
        cls.running_loggers[key] = instance
        return instance

    @classmethod
    def fetch(cls, key: str):
        return cls.running_loggers.get(key)

    def bind(self, **kwargs) -> "Logger":
        self._logger = self._logger.bind(**kwargs)
        return self

    def set_level(self, level: str):
        normalized = str(level).upper()
        if normalized not in levels.level_names:
            self.log_level = levels.INFO
            self.error(f"未知的日志等级：{level}，已回退到 INFO")
        else:
            self.log_level = normalized
        return self

    @staticmethod
    def format_exec() -> str:
        return traceback.format_exc()

    def register_hook(self) -> None:
        previous_hook = sys.excepthook

        def hook(
                exc_type: type[BaseException],
                exc_value: BaseException,
                exc_tb: typing.Any,
        ) -> None:
            if issubclass(exc_type, KeyboardInterrupt):
                previous_hook(exc_type, exc_value, exc_tb)
                return
            self._logger.opt(exception=(exc_type, exc_value, exc_tb)).critical("未捕获异常")

        sys.excepthook = hook

    def _enabled(self, level: str) -> bool:
        return levels.level_nums[level] >= levels.level_nums[self.log_level]

    def _emit(self, message: typing.Any, level: str) -> None:
        normalized = str(level).upper()
        if normalized not in levels.level_names:
            raise ValueError(f"Unsupported log level: {level}")
        if self._enabled(normalized):
            self._logger.opt(depth=2).log(normalized, str(message))

    def log(self, message: typing.Any, level: str = levels.INFO) -> None:
        self._emit(message, level)

    def info(self, message: typing.Any) -> None:
        self._emit(message, levels.INFO)

    def warning(self, message: typing.Any) -> None:
        self._emit(message, levels.WARNING)

    def error(self, message: typing.Any) -> None:
        self._emit(message, levels.ERROR)

    def critical(self, message: typing.Any) -> None:
        self._emit(message, levels.CRITICAL)

    def debug(self, message: typing.Any) -> None:
        self._emit(message, levels.DEBUG)

    def trace(self, message: typing.Any) -> None:
        self._emit(message, levels.TRACE)

    def exception(self, message: typing.Any) -> None:
        if self._enabled(levels.ERROR):
            self._logger.opt(depth=1, exception=True).error(str(message))
