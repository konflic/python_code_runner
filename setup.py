from setuptools import setup, find_packages

setup(
    name="python-runner",
    version="1.0-4",
    packages=find_packages(),
    install_requires=[
        'PyGObject',
    ],
    data_files=[
        ('share/applications', ['data/python-runner.desktop']),
        ('share/icons/hicolor/scalable/apps', ['data/icons/python-runner.svg']),
        ('share/glib-2.0/schemas', ['data/glib-2.0/schemas/com.example.python-runner.gschema.xml']),
    ],
    entry_points={
        'console_scripts': [
            'python_runner=python_runner.main:main',
        ],
    },
)