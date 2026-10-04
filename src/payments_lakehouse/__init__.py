"""Shared Python code for the payments fraud-monitoring lakehouse.

Anything that can be unit-tested without a Databricks cluster (data generator,
API clients, validation helpers) lives in this package.
"""

from importlib.metadata import version

# Single source of truth for the version is pyproject.toml.
__version__ = version("payments-lakehouse")
