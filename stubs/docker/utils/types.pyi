# Stubs for docker.utils.types (Python 2)
#
# NOTE: This dynamically typed stub was automatically generated by stubgen.

from typing import Any

class LogConfigTypesEnum:
    JSON = ... # type: Any
    SYSLOG = ... # type: Any
    JOURNALD = ... # type: Any
    GELF = ... # type: Any
    FLUENTD = ... # type: Any
    NONE = ... # type: Any

class DictType(dict):
    def __init__(self, init): ...

class LogConfig(DictType):
    types = ... # type: Any
    def __init__(self, **kwargs): ...
    @property
    def type(self): ...
    @type.setter
    def type(self, value): ...
    @property
    def config(self): ...
    def set_config_value(self, key, value): ...
    def unset_config(self, key): ...

class Ulimit(DictType):
    def __init__(self, **kwargs): ...
    @property
    def name(self): ...
    @name.setter
    def name(self, value): ...
    @property
    def soft(self): ...
    @soft.setter
    def soft(self, value): ...
    @property
    def hard(self): ...
    @hard.setter
    def hard(self, value): ...