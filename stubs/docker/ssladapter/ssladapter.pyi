# Stubs for docker.ssladapter.ssladapter (Python 2)
#
# NOTE: This dynamically typed stub was automatically generated by stubgen.

from typing import Any
from requests.adapters import HTTPAdapter
from .ssl_match_hostname import match_hostname as match_hostname

PoolManager = ... # type: Any

class SSLAdapter(HTTPAdapter):
    ssl_version = ... # type: Any
    assert_hostname = ... # type: Any
    assert_fingerprint = ... # type: Any
    def __init__(self, ssl_version=None, assert_hostname=None, assert_fingerprint=None, **kwargs): ...
    poolmanager = ... # type: Any
    def init_poolmanager(self, connections, maxsize, block=False): ...
    def get_connection(self, *args, **kwargs): ...
    def can_override_ssl_version(self): ...
