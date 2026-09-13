from jianer.configurator import BotConfig, BotFeishuC, BotHTTPC, BotWSC


def test_bot_config_reads_active_protocol_from_connections():
    config = BotConfig(
        protocol="Milky",
        connections={
            "Milky": {
                "mode": "FWS",
                "host": "127.0.0.1",
                "port": 3001,
            }
        },
    )

    connection = config.get_connection()

    assert isinstance(connection, BotWSC)
    assert connection.host == "127.0.0.1"
    assert connection.port == 3001
    assert not hasattr(config, "connection")


def test_bot_config_get_connection_by_protocol_and_alias():
    config = BotConfig(
        protocol="Milky",
        connections={
            "onebotv11": {"mode": "FWS", "host": "127.0.0.1", "port": 5004},
            "Milky": {
                "mode": "HTTPC",
                "host": "127.0.0.1",
                "port": 3010,
                "listener_host": "127.0.0.1",
                "listener_port": 5003,
            },
            "Feishu": {"app_id": "cli_x", "app_secret": "secret"},
        },
    )

    onebot = config.get_connection("OneBot")
    assert isinstance(onebot, BotWSC)
    assert onebot.port == 5004

    milky = config.get_connection("milky")
    assert isinstance(milky, BotHTTPC)
    assert milky.listener_port == 5003

    assert isinstance(config.get_connection("Feishu"), BotFeishuC)
    assert config.get_connection("Kritor") is None


def test_bot_config_ignores_legacy_single_connection():
    config = BotConfig(
        protocol="OneBot",
        connections={
            "OneBot": {"mode": "FWS", "host": "127.0.0.1", "port": 5004},
        },
        connection={
            "mode": "HTTPC",
            "host": "10.0.0.1",
            "port": 3000,
            "listener_host": "127.0.0.1",
            "listener_port": 8080,
        },
    )

    connection = config.get_connection()

    assert isinstance(connection, BotWSC)
    assert connection.host == "127.0.0.1"
    assert connection.port == 5004
    assert not hasattr(config, "connection")


def test_bot_config_without_connections_returns_none():
    config = BotConfig(protocol="OneBot")

    assert config.get_connection() is None
