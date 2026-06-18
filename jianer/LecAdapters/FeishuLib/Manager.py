import random

from ... import configurator, hyperogger
from ...utils import logic

reports = logic.KeyQueue()

config: configurator.BotConfig
logger: hyperogger.Logger


def init() -> None:
    global config, logger
    config = configurator.BotConfig.get("jianer-bot")
    logger = hyperogger.Logger()
    logger.set_level(config.log_level if config else "INFO")


def normalize_response(response: dict) -> dict:
    code = response.get("code", 0) if isinstance(response, dict) else -1
    data = response.get("data") if isinstance(response, dict) else None
    if data is None and isinstance(response, dict):
        data = response.get("bot")
    if data is None:
        data = {}
    return {
        "status": "ok" if code == 0 else "failed",
        "retcode": code,
        "msg": response.get("msg", "") if isinstance(response, dict) else str(response),
        "data": data,
        "raw": response,
    }


class Packet:
    def __init__(self, endpoint: str, **kwargs):
        self.endpoint = endpoint
        self.paras = kwargs
        self.echo = f"{endpoint}_{random.randint(1000, 9999)}"

    def send_to(self, connection) -> dict:
        response = connection.call(self.endpoint, **self.paras)
        normalized = normalize_response(response)
        normalized["echo"] = self.echo
        reports.put(self.echo, normalized)
        return normalized

