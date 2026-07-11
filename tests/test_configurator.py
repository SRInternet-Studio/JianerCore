from jianer.configurator import BotConfig, BotHTTPC, BotWSC


def test_bot_config_accepts_connections_without_legacy_connection():
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
    assert config.connection is connection
    assert connection.host == "127.0.0.1"
    assert connection.port == 3001


def test_bot_config_keeps_legacy_single_connection_support():
    config = BotConfig(
        protocol="Milky",
        connection={
            "mode": "HTTPC",
            "host": "127.0.0.1",
            "port": 3000,
            "listener_host": "127.0.0.1",
            "listener_port": 8080,
        },
    )

    assert isinstance(config.get_connection(), BotHTTPC)
