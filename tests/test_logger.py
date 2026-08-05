import sys

from loguru import logger as loguru_logger

from jianer import hyperogger


Logger = hyperogger.Logger


def test_logger_configures_native_loguru_console_sink(monkeypatch):
    class FakeLoguru:
        def __init__(self):
            self.removed = []
            self.sink = None
            self.options = None
            self.configure_options = None

        def configure(self, **options):
            self.configure_options = options

        def remove(self, handler_id):
            self.removed.append(handler_id)

        def add(self, sink, **options):
            self.sink = sink
            self.options = options

    fake_loguru = FakeLoguru()
    monkeypatch.setattr(hyperogger, "loguru_logger", fake_loguru)

    hyperogger._configure_loguru()

    assert fake_loguru.removed == [0]
    assert fake_loguru.configure_options["patcher"] is hyperogger.patch_log_record
    assert fake_loguru.sink is sys.stderr
    assert fake_loguru.options["level"] == "TRACE"
    assert "format" not in fake_loguru.options
    assert "colorize" not in fake_loguru.options


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


def test_logger_reports_the_original_call_site():
    messages = []
    sink_id = loguru_logger.add(messages.append, level="TRACE")
    logger = Logger("INFO")

    try:
        logger.info("call-site-check")
    finally:
        loguru_logger.remove(sink_id)

    assert len(messages) == 1
    assert messages[0].record["name"] == __name__
    assert messages[0].record["function"] == "test_logger_reports_the_original_call_site"
