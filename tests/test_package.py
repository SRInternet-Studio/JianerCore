from importlib.metadata import requires, version

import jianer


def test_package_identity():
    assert version("jianer-bot") == jianer.JIANER_BOT_VERSION
    assert jianer.__name__ == "jianer"


def test_aiohttp_dependency_is_unpinned():
    aiohttp_requirements = [
        requirement
        for requirement in requires("jianer-bot") or ()
        if requirement.lower().startswith("aiohttp")
    ]

    assert aiohttp_requirements == ["aiohttp"]
