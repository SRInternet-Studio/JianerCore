from cfgr.manager import BaseConfig


def _canon_protocol(protocol: str) -> str:
    mapping = {
        "onebot": "OneBot",
        "onebot11": "OneBot",
        "onebotv11": "OneBot",
        "onebot-v11": "OneBot",
        "milky": "Milky",
        "kritor": "Kritor",
        "feishu": "Feishu",
        "lark": "Feishu",
    }
    return mapping.get(str(protocol or "OneBot").strip().lower(), str(protocol or "OneBot").strip())


def _conn_value(connection, name: str, default=None):
    if isinstance(connection, dict):
        return connection.get(name, default)
    return getattr(connection, name, default)


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
    event_mode: str = None
    app_id: str = None
    app_secret: str = None
    verification_token: str = None
    encrypt_key: str = None
    callback_path: str = None
    base_url: str = None
    token_refresh_skew_seconds: int = None
    bot_open_id: str = None


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
    event_mode: str = None
    app_id: str = None
    app_secret: str = None
    verification_token: str = None
    encrypt_key: str = None
    callback_path: str = None
    base_url: str = None
    token_refresh_skew_seconds: int = None
    bot_open_id: str = None


class BotFeishuC(BaseConfig):
    mode: str = "OAPI"
    app_id: str
    app_secret: str
    host: str = "0.0.0.0"
    port: int = 8080
    endpoint: str = "/"
    verification_token: str = None
    encrypt_key: str = None
    base_url: str = "https://open.feishu.cn"
    user_id_type: str = "open_id"
    tenant_access_token: str = None
    event_mode: str = "webhook"
    callback_path: str = "/feishu/callback"
    token_refresh_skew_seconds: int = 300
    bot_open_id: str = None
    log_level: str = "INFO"


class BotConfig(BaseConfig):
    protocol: str = "OneBot"
    owner: list
    black_list: list
    silents: list
    Connections: dict
    connections: dict
    connection: BotHTTPC
    connection: BotWSC
    connection: BotFeishuC
    connection: dict
    log_level: str = "INFO"
    log_use_nf: bool = False
    uin: int
    max_workers: int
    others: dict

    def _build_connection(self, protocol: str, connection):
        if connection is None:
            return None
        if isinstance(connection, (BotHTTPC, BotWSC, BotFeishuC)):
            return connection
        if not isinstance(connection, dict):
            return connection

        protocol = _canon_protocol(protocol)
        mode = str(connection.get("mode", "") or "").upper()
        if protocol == "Feishu" and mode not in ("FWS", "HTTPC"):
            return BotFeishuC(**connection)
        if mode == "HTTPC":
            return BotHTTPC(**connection)
        if mode == "FWS":
            return BotWSC(**connection)
        if protocol == "Feishu":
            return BotFeishuC(**connection)
        return BotWSC(**connection)

    def get_connection(self, protocol: str = None) -> object:
        protocol = _canon_protocol(protocol or self.protocol)
        for attr in ("connections", "Connections"):
            connections = getattr(self, attr, None)
            if isinstance(connections, dict):
                for key, value in connections.items():
                    if _canon_protocol(key) == protocol:
                        return self._build_connection(protocol, value)
        return self._build_connection(protocol, getattr(self, "connection", None))

    def custom_post(self, **kwargs):
        connections = getattr(self, "connections", None)
        if connections is None:
            connections = getattr(self, "Connections", None)
        if isinstance(connections, dict):
            parsed_connections = {}
            for protocol, connection in connections.items():
                parsed_connections[_canon_protocol(protocol)] = self._build_connection(protocol, connection)
            self.connections = parsed_connections
            if getattr(self, "connection", None) is None:
                active_protocol = _canon_protocol(getattr(self, "protocol", "OneBot"))
                active_connection = parsed_connections.get(active_protocol)
                if active_connection is not None:
                    self.connection = active_connection

        connection = getattr(self, "connection", None)
        if connection is None or isinstance(connection, (BotHTTPC, BotWSC, BotFeishuC)):
            return

        if self.protocol == "OneBot":
            if _conn_value(connection, "mode") == "FWS":
                self.connection = BotWSC(**connection)
            elif _conn_value(connection, "mode") == "HTTPC":
                self.connection = BotHTTPC(**connection)
        elif self.protocol == "Kritor":
            self.connection = BotWSC(**connection)
        elif self.protocol == "Milky":
            if _conn_value(connection, "mode") == "FWS":
                self.connection = BotWSC(**connection)
            elif _conn_value(connection, "mode") == "HTTPC":
                self.connection = BotHTTPC(**connection)
        elif self.protocol == "Feishu":
            self.connection = BotFeishuC(**connection)
