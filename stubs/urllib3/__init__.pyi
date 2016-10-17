# Stubs for urllib3 (Python 2)
#
# NOTE: This dynamically typed stub was automatically generated by stubgen.

from typing import Any
from .connectionpool import HTTPConnectionPool as HTTPConnectionPool, HTTPSConnectionPool as HTTPSConnectionPool, connection_from_url as connection_from_url
import logging

__license__ = ... # type: Any

class NullHandler(logging.Handler):
    def emit(self, record): ...

def add_stderr_logger(level=...): ...
def disable_warnings(category=...): ...