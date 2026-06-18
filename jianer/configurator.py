from cfgr.manager import BaseConfig


class BotWSC(BaseConfig):
    mode: str = "FWS"
    ob_auto_startup: bool = False
    ob_exec: str = None
    ob_startup_path: str = None
    ob_log_output: bool = False
    host: str
    port: int
    retries: int = 5
    token: str
    auth: str


class BotHTTPC(BaseConfig):
    mode: str = "HTTPC"
    ob_auto_startup: bool = False
    ob_exec: str = None
    ob_startup_path: str = None
    ob_log_output: bool = False
    host: str
    port: int
    listener_host: str
    listener_port: int
    retries: int = 5
    auth: str


class BotFeishuC(BaseConfig):
    mode: str = "FEISHU"
    app_id: str
    app_secret: str
    host: str = "0.0.0.0"
    port: int = 8080
    endpoint: str = "/"
    verification_token: str = None
    base_url: str = "https://open.feishu.cn"
    user_id_type: str = "open_id"
    tenant_access_token: str = None


class BotConfig(BaseConfig):
    protocol: str = "OneBot"
    owner: list
    black_list: list
    silents: list
    connection: BotHTTPC
    connection: BotWSC
    connection: BotFeishuC
    connection: dict
    log_level: str = "INFO"
    log_use_nf: bool = False
    uin: int
    max_workers: int
    others: dict

    def custom_post(self, **kwargs):
        if self.protocol == "OneBot":
            if self.connection["mode"] == "FWS":
                self.connection = BotWSC(**self.connection)
            elif self.connection["mode"] == "HTTPC":
                self.connection = BotHTTPC(**self.connection)
        elif self.protocol == "Kritor":
            self.connection = BotWSC(**self.connection)
        elif self.protocol == "Milky":
            if self.connection["mode"] == "FWS":
                self.connection = BotWSC(**self.connection)
            elif self.connection["mode"] == "HTTPC":
                self.connection = BotHTTPC(**self.connection)
        elif self.protocol == "Feishu":
            self.connection = BotFeishuC(**self.connection)
