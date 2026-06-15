from loguru import logger as loguru_logger

from jianer.hyperogger import Logger


def test_logger_level_filtering():
    messages = []
    sink_id = loguru_logger.add(messages.append, format="{message}", level="TRACE")
    logger = Logger("WARNING")

    try:
        logger.info("hidden-info")
        logger.warning("visible-warning")
    finally:
        loguru_logger.remove(sink_id)

    output = "".join(messages)
    assert "hidden-info" not in output
    assert "visible-warning" in output


def test_named_logger_registry():
    logger = Logger.create("test-logger", "DEBUG")

    assert Logger.fetch("test-logger") is logger
