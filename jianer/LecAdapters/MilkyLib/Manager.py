import random

from ... import configurator, hyperogger
from ...utils import logic
from .translator import MilkyHttpConnection

reports = logic.KeyQueue()

config: configurator.BotConfig
logger: hyperogger.Logger


def init() -> None:
    global config, logger
    config = configurator.BotConfig.get("jianer-bot")
    logger = hyperogger.Logger()
    logger.set_level(config.log_level)


class Packet:
    def __init__(self, endpoint: str, **kwargs):
        self.endpoint = endpoint
        self.paras = kwargs
        self.echo = f"{endpoint}_{random.randint(1000, 9999)}"

    def send_to(self, connection: MilkyHttpConnection) -> dict:
        if not isinstance(connection, MilkyHttpConnection):
            raise ValueError(f"Invalid connection: {connection}")

        res = connection.http_send(self.endpoint, self.paras)
        if isinstance(res, dict):
            res["echo"] = self.echo
            reports.put(self.echo, res)
        return res
