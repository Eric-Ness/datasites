"""Shared engine for datasites pipelines: fetch, change detection, validation and atomic publishing."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("datasites-core")
except PackageNotFoundError:  # running from a source checkout without installing
    __version__ = "0.0.0"
