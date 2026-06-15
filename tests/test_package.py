from importlib.metadata import version

import jianer


def test_package_identity():
    assert version("jianer-bot") == jianer.JIANER_BOT_VERSION
    assert jianer.__name__ == "jianer"
