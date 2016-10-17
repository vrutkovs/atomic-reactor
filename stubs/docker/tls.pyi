# Stubs for docker.tls (Python 2)
#
# NOTE: This dynamically typed stub was automatically generated by stubgen.

from typing import Any
from .ssladapter import ssladapter as ssladapter

class TLSConfig:
    cert = ... # type: Any
    ca_cert = ... # type: Any
    verify = ... # type: Any
    ssl_version = ... # type: Any
    assert_hostname = ... # type: Any
    assert_fingerprint = ... # type: Any
    def __init__(self, client_cert=None, ca_cert=None, verify=None, ssl_version=None, assert_hostname=None, assert_fingerprint=None): ...
    def configure_client(self, client): ...
