from setuptools import setup, find_packages
from python_runner.version import VERSION

setup(
    name="python-runner",
    version=VERSION,
    packages=find_packages(),
    install_requires=[
        "PyGObject",
    ],
    data_files=[
        ("share/applications", ["data/python-runner.desktop"]),
        ("share/icons/hicolor/scalable/apps", ["data/icons/python-runner.svg"]),
    ],
    entry_points={
        "console_scripts": [
            "python_runner=python_runner.main:main",
        ],
    },
)
