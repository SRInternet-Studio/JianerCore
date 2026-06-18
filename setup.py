import re
from pathlib import Path

from setuptools import find_namespace_packages, setup


PACKAGE_ROOT = Path(__file__).parent
VERSION_SOURCE = (PACKAGE_ROOT / "jianer" / "__init__.py").read_text(encoding="utf-8")
VERSION = re.search(r'^JIANER_BOT_VERSION = "([^"]+)"$', VERSION_SOURCE, re.MULTILINE).group(1)
LONG_DESCRIPTION = (PACKAGE_ROOT / "README.md").read_text(encoding="utf-8")


setup(
    name="jianer-bot",
    version=VERSION,
    description="Jianer_QQ_bot 使用的可扩展 QQ 机器人框架",
    long_description=LONG_DESCRIPTION,
    long_description_content_type="text/markdown",
    author="SRInternet-Studio",
    url="https://github.com/SRInternet-Studio/JianerCore",
    project_urls={
        "Source": "https://github.com/SRInternet-Studio/JianerCore",
        "Issues": "https://github.com/SRInternet-Studio/JianerCore/issues",
        "Jianer_QQ_bot": "https://github.com/SRInternet-Studio/Jianer_QQ_bot",
    },
    packages=find_namespace_packages(include=["jianer", "jianer.*"]),
    install_requires=[
        "aiohttp~=3.9.5",
        "requests~=2.31.0",
        "httpx~=0.26.0",
        "lark-oapi~=1.6.8",
        "loguru~=0.7.3",
        "grpclib~=0.4.7",
        "betterproto~=2.0.0b7",
        "websocket-client~=1.8.0",
        "Flask~=3.0.0",
        "google~=3.0.0",
        "protobuf~=4.25.3",
        "ucfgr",
        "PyYAML",
    ],
    python_requires=">=3.9",
    license="GPL-3.0-only",
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Framework :: AsyncIO",
        "License :: OSI Approved :: GNU General Public License v3 (GPLv3)",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: Communications :: Chat",
        "Typing :: Typed",
    ],
    include_package_data=True,
)
