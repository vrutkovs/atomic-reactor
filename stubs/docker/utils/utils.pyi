# Stubs for docker.utils.utils (Python 2)
#
# NOTE: This dynamically typed stub was automatically generated by stubgen.

from typing import Any
from .types import Ulimit as Ulimit, LogConfig as LogConfig

DEFAULT_HTTP_HOST = ... # type: Any
DEFAULT_UNIX_SOCKET = ... # type: Any
BYTE_UNITS = ... # type: Any

def create_ipam_pool(subnet=None, iprange=None, gateway=None, aux_addresses=None): ...
def create_ipam_config(driver='', pool_configs=None): ...
def mkbuildcontext(dockerfile): ...
def decode_json_header(header): ...
def tar(path, exclude=None, dockerfile=None, fileobj=None, gzip=False): ...
def exclude_paths(root, patterns, dockerfile=None): ...
def should_include(path, exclude_patterns, include_patterns): ...
def get_paths(root, exclude_patterns, include_patterns, has_exceptions=False): ...
def match_path(path, pattern): ...
def compare_version(v1, v2): ...
def version_lt(v1, v2): ...
def version_gte(v1, v2): ...
def ping_registry(url): ...
def ping(url, valid_4xx_statuses=None): ...
def convert_port_bindings(port_bindings): ...
def convert_volume_binds(binds): ...
def convert_tmpfs_mounts(tmpfs): ...
def parse_repository_tag(repo_name): ...
def parse_host(addr, platform=None, tls=False): ...
def parse_devices(devices): ...
def kwargs_from_env(ssl_version=None, assert_hostname=None, environment=None): ...
def convert_filters(filters): ...
def datetime_to_timestamp(dt): ...
def longint(n): ...
def parse_bytes(s): ...
def host_config_type_error(param, param_value, expected): ...
def host_config_version_error(param, version, less_than=True): ...
def host_config_value_error(param, param_value): ...
def create_host_config(binds=None, port_bindings=None, lxc_conf=None, publish_all_ports=False, links=None, privileged=False, dns=None, dns_search=None, volumes_from=None, network_mode=None, restart_policy=None, cap_add=None, cap_drop=None, devices=None, extra_hosts=None, read_only=None, pid_mode=None, ipc_mode=None, security_opt=None, ulimits=None, log_config=None, mem_limit=None, memswap_limit=None, mem_swappiness=None, cgroup_parent=None, group_add=None, cpu_quota=None, cpu_period=None, oom_kill_disable=False, shm_size=None, version=None, tmpfs=None, oom_score_adj=None): ...
def normalize_links(links): ...
def create_networking_config(endpoints_config=None): ...
def create_endpoint_config(version, aliases=None, links=None): ...
def parse_env_file(env_file): ...
def split_command(command): ...
def format_environment(environment): ...
def create_container_config(version, image, command, hostname=None, user=None, detach=False, stdin_open=False, tty=False, mem_limit=None, ports=None, environment=None, dns=None, volumes=None, volumes_from=None, network_disabled=False, entrypoint=None, cpu_shares=None, working_dir=None, domainname=None, memswap_limit=None, cpuset=None, host_config=None, mac_address=None, labels=None, volume_driver=None, stop_signal=None, networking_config=None): ...
