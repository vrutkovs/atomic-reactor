# Stubs for json.encoder (Python 2)
#
# NOTE: This dynamically typed stub was automatically generated by stubgen.

from typing import Any

ESCAPE = ... # type: Any
ESCAPE_ASCII = ... # type: Any
HAS_UTF8 = ... # type: Any
ESCAPE_DCT = ... # type: Any
INFINITY = ... # type: Any
FLOAT_REPR = ... # type: Any

def encode_basestring(s): ...
def py_encode_basestring_ascii(s): ...

encode_basestring_ascii = ... # type: Any

class JSONEncoder:
    item_separator = ... # type: Any
    key_separator = ... # type: Any
    skipkeys = ... # type: Any
    ensure_ascii = ... # type: Any
    check_circular = ... # type: Any
    allow_nan = ... # type: Any
    sort_keys = ... # type: Any
    indent = ... # type: Any
    encoding = ... # type: Any
    def __init__(self, skipkeys=False, ensure_ascii=True, check_circular=True, allow_nan=True, sort_keys=False, indent=None, separators=None, encoding='', default=None): ...
    def default(self, o): ...
    def encode(self, o): ...
    def iterencode(self, o, _one_shot=False): ...