"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""

from __future__ import print_function, unicode_literals

import hashlib
import json
import jsonschema
import os
import re
from pipes import quote
import requests
from requests.exceptions import ConnectionError, SSLError, HTTPError, RetryError, Timeout
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util import Retry
import shutil
import subprocess
import tempfile
import logging
import uuid
import yaml
import codecs
import string

from six.moves.urllib.parse import urlparse

from atomic_reactor.constants import DOCKERFILE_FILENAME, FLATPAK_FILENAME, TOOLS_USED,\
                                     INSPECT_CONFIG, IMAGE_TYPE_OCI,\
                                     HTTP_MAX_RETRIES, HTTP_BACKOFF_FACTOR,\
                                     HTTP_CLIENT_STATUS_RETRY, HTTP_REQUEST_TIMEOUT

from dockerfile_parse import DockerfileParser
from pkg_resources import resource_stream

from importlib import import_module
from requests.utils import guess_json_utf

logger = logging.getLogger(__name__)


class ImageName(object):
    def __init__(self, registry=None, namespace=None, repo=None, tag=None):
        self.registry = registry
        self.namespace = namespace
        self.repo = repo
        self.tag = tag

    @classmethod
    def parse(cls, image_name):
        result = cls()

        # registry.org/namespace/repo:tag
        s = image_name.split('/', 2)

        if len(s) == 2:
            if '.' in s[0] or ':' in s[0]:
                result.registry = s[0]
            else:
                result.namespace = s[0]
        elif len(s) == 3:
            result.registry = s[0]
            result.namespace = s[1]
        result.repo = s[-1]

        for sep in '@:':
            try:
                result.repo, result.tag = result.repo.rsplit(sep, 1)
            except ValueError:
                continue
            break

        return result

    def to_str(self, registry=True, tag=True, explicit_tag=False,
               explicit_namespace=False):
        if self.repo is None:
            raise RuntimeError('No image repository specified')

        result = self.repo

        if tag and self.tag and ':' in self.tag:
            result = '{0}@{1}'.format(result, self.tag)
        elif tag and self.tag:
            result = '{0}:{1}'.format(result, self.tag)
        elif tag and explicit_tag:
            result = '{0}:{1}'.format(result, 'latest')

        if self.namespace:
            result = '{0}/{1}'.format(self.namespace, result)
        elif explicit_namespace:
            result = '{0}/{1}'.format('library', result)

        if registry and self.registry:
            result = '{0}/{1}'.format(self.registry, result)

        return result

    @property
    def pulp_repo(self):
        return self.to_str(registry=False, tag=False).replace("/", "-")

    def __str__(self):
        return self.to_str(registry=True, tag=True)

    def __repr__(self):
        return "ImageName(image=%r)" % self.to_str()

    def __eq__(self, other):
        return type(self) == type(other) and self.__dict__ == other.__dict__

    def __ne__(self, other):
        return not self == other

    def __hash__(self):
        return hash(self.to_str())

    def copy(self):
        return ImageName(
            registry=self.registry,
            namespace=self.namespace,
            repo=self.repo,
            tag=self.tag)


def figure_out_build_file(absolute_path, local_path=None):
    """
    try to figure out the build file (Dockerfile or flatpak.json) from provided
    path and optionally from relative local path this is meant to be used with
    git repo: absolute_path is path to git repo, local_path is path to dockerfile
    within git repo

    :param absolute_path:
    :param local_path:
    :return: tuple, (dockerfile_path, dir_with_dockerfile_path)
    """
    logger.info("searching for dockerfile in '%s' (local path %s)", absolute_path, local_path)
    logger.debug("abs path = '%s', local path = '%s'", absolute_path, local_path)
    if local_path:
        if local_path.endswith(DOCKERFILE_FILENAME) or local_path.endswith(FLATPAK_FILENAME):
            git_build_file_dir = os.path.dirname(local_path)
            build_file_dir = os.path.abspath(os.path.join(absolute_path, git_build_file_dir))
        else:
            build_file_dir = os.path.abspath(os.path.join(absolute_path, local_path))
    else:
        build_file_dir = os.path.abspath(absolute_path)
    if not os.path.isdir(build_file_dir):
        raise IOError("Directory '%s' doesn't exist." % build_file_dir)
    # Check for flatpak.json first because we do flatpak.json => Dockerfile generation
    build_file_path = os.path.join(build_file_dir, FLATPAK_FILENAME)
    if os.path.isfile(build_file_path):
        logger.debug("flatpak.json found: '%s'", build_file_path)
        return build_file_path, build_file_dir
    build_file_path = os.path.join(build_file_dir, DOCKERFILE_FILENAME)
    if os.path.isfile(build_file_path):
        logger.debug("Dockerfile found: '%s'", build_file_path)
        return build_file_path, build_file_dir
    raise IOError("Dockerfile '%s' doesn't exist." % build_file_path)


class CommandResult(object):
    def __init__(self):
        self._logs = []
        self._parsed_logs = []
        self._error = None
        self._error_detail = None

    def parse_item(self, item):
        """
        :param item: dict, decoded log data
        """
        # append here just in case .get bellow fails
        self._parsed_logs.append(item)

        # make sure the log item is a dictionary object
        if isinstance(item, dict):
            line = item.get("stream", "")
        else:
            line = item
            item = None

        for l in line.splitlines():
            l = l.strip()
            self._logs.append(l)
            if l:
                logger.debug(l)

        if item is not None:
            self._error = item.get("error", None)
            self._error_detail = item.get("errorDetail", None)
            if self._error:
                logger.error(item)

    @property
    def parsed_logs(self):
        return self._parsed_logs

    @property
    def logs(self):
        return self._logs

    @property
    def error(self):
        return self._error

    @property
    def error_detail(self):
        return self._error_detail

    def is_failed(self):
        return bool(self.error) or bool(self.error_detail)


def wait_for_command(logs_generator):
    """
    Create a CommandResult from given iterator

    :return: CommandResult
    """
    logger.info("wait_for_command")
    cr = CommandResult()
    for item in logs_generator:
        cr.parse_item(item)

    logger.info("no more logs")
    return cr


def clone_git_repo(git_url, target_dir, commit=None):
    """
    clone provided git repo to target_dir, optionally checkout provided commit

    :param git_url: str, git repo to clone
    :param target_dir: str, filesystem path where the repo should be cloned
    :param commit: str, commit to checkout, SHA-1 or ref
    :return: str, commit ID of HEAD
    """
    commit = commit or "master"
    logger.info("cloning git repo '%s'", git_url)
    logger.debug("url = '%s', dir = '%s', commit = '%s'",
                 git_url, target_dir, commit)

    cmd = ["git", "clone", "-b", commit, "--depth", "1", git_url, quote(target_dir)]
    logger.debug("doing a shallow clone '%s'", cmd)
    try:
        subprocess.check_call(cmd)
    except subprocess.CalledProcessError as ex:
        logger.warning(repr(ex))
        # http://stackoverflow.com/questions/1911109/clone-a-specific-git-branch/4568323#4568323
        # -b takes only refs, not SHA-1
        cmd = ["git", "clone", "-b", commit, "--single-branch", git_url, quote(target_dir)]
        logger.debug("cloning single branch '%s'", cmd)
        try:
            subprocess.check_call(cmd)
        except subprocess.CalledProcessError as ex:
            logger.warning(repr(ex))
            # let's try again with plain `git clone $url && git checkout`
            cmd = ["git", "clone", git_url, quote(target_dir)]
            logger.debug("cloning '%s'", cmd)
            subprocess.check_call(cmd)
            cmd = ["git", "reset", "--hard", commit]
            logger.debug("checking out branch '%s'", cmd)
            subprocess.check_call(cmd, cwd=target_dir)
    cmd = ["git", "rev-parse", "HEAD"]
    logger.debug("getting SHA-1 of provided ref '%s'", cmd)
    commit_id = subprocess.check_output(cmd, cwd=target_dir)
    commit_id = commit_id.strip()
    logger.info("commit ID = %s", commit_id)
    return commit_id


class LazyGit(object):
    """
    usage:

        lazy_git = LazyGit(git_url="...")
        with lazy_git:
            laze_git.git_path

    or

        lazy_git = LazyGit(git_url="...", tmpdir=tmp_dir)
        lazy_git.git_path
    """
    def __init__(self, git_url, commit=None, tmpdir=None):
        self.git_url = git_url
        # provided commit ID/reference to check out
        self.commit = commit
        # commit ID of HEAD; we'll figure this out ourselves
        self._commit_id = None
        self.provided_tmpdir = tmpdir
        self._git_path = None

    @property
    def _tmpdir(self):
        return self.provided_tmpdir or self.our_tmpdir

    @property
    def commit_id(self):
        return self._commit_id

    @property
    def git_path(self):
        if self._git_path is None:
            self._commit_id = clone_git_repo(self.git_url, self._tmpdir, self.commit)
            self._git_path = self._tmpdir
        return self._git_path

    def __enter__(self):
        if not self.provided_tmpdir:
            self.our_tmpdir = tempfile.mkdtemp()

    def __exit__(self, exc_type, exc_val, exc_tb):
        if not self.provided_tmpdir:
            if self.our_tmpdir:
                shutil.rmtree(self.our_tmpdir)


def escape_dollar(v):
    try:
        str_type = unicode
    except NameError:
        str_type = str
    if isinstance(v, str_type):
        return v.replace('$', r'\$')
    else:
        return v


def render_yum_repo(repo, escape_dollars=True):
    repo.setdefault("name", str(uuid.uuid4().hex[:6]))
    repo_name = repo["name"]
    logger.info("rendering repo '%s'", repo_name)
    rendered_repo = '[%s]\n' % repo_name
    for key, value in repo.items():
        if escape_dollars:
            value = escape_dollar(value)
        rendered_repo += "%s=%s\n" % (key, value)
    logger.info("rendered repo: %r", rendered_repo)
    return rendered_repo


def process_substitutions(mapping, substitutions):
    """Process `substitutions` for given `mapping` (modified in place)

    :param mapping: a dict
    :param substitutions: either a dict {key: value} or a list of ["key=value"] strings
        keys can use dotted notation to change to nested dicts

    Note: Plugin substitutions are processed differently - they are accepted in form of
        plugin_type.plugin_name.arg_name, even though that doesn't reflect the actual
        structure of given mapping.
    Also note: For non-plugin substitutions, additional dicts/key/value pairs
        are created on the way if they're missing. For plugin substitutions, only
        existing values can be changed (TODO: do we want to change this behaviour?).
    """
    def parse_val(v):
        # TODO: do we need to recognize numbers,lists,dicts?
        if v.lower() == 'true':
            return True
        elif v.lower() == 'false':
            return False
        elif v.lower() == 'none':
            return None
        return v

    if isinstance(substitutions, list):
        # if we got a list, get a {key: val} dict out of it
        substitutions = dict([s.split('=', 1) for s in substitutions])

    for key, val in substitutions.items():
        cur_dict = mapping
        key_parts = key.split('.')
        if key_parts[0].endswith('_plugins'):
            _process_plugin_substitution(mapping, key_parts, val)
        else:
            key_parts_without_last = key_parts[:-1]

            # now go down mapping, following the dotted path; create empty dicts on way
            for k in key_parts_without_last:
                if k in cur_dict:
                    if not isinstance(cur_dict[k], dict):
                        cur_dict[k] = {}
                else:
                    cur_dict[k] = {}
                cur_dict = cur_dict[k]
            cur_dict[key_parts[-1]] = parse_val(val)


def _process_plugin_substitution(mapping, key_parts, value):
    try:
        plugin_type, plugin_name, arg_name = key_parts
    except ValueError:
        logger.error("invalid absolute path '%s': it requires exactly three parts: "
                     "plugin type, plugin name, argument name (dot separated)",
                     key_parts)
        raise ValueError("invalid absolute path to plugin, it should be "
                         "plugin_type.plugin_name.argument_name")

    logger.debug("getting plugin conf for '%s' with type '%s'",
                 plugin_name, plugin_type)
    plugins_of_a_type = mapping.get(plugin_type, None)
    if plugins_of_a_type is None:
        logger.warning("there are no plugins with type '%s'",
                       plugin_type)
        return
    plugin_conf = [x for x in plugins_of_a_type if x['name'] == plugin_name]
    plugins_num = len(plugin_conf)
    if plugins_num == 1:
        if arg_name not in plugin_conf[0]['args']:
            logger.warning("no configuration value '%s' for plugin '%s', skipping",
                           arg_name, plugin_name)
            return
        logger.info("changing value '%s' of plugin '%s': '%s' -> '%s'",
                    arg_name, plugin_name, plugin_conf[0]['args'][arg_name], value)
        plugin_conf[0]['args'][arg_name] = value
    elif plugins_num <= 0:
        logger.warning("there is no configuration for plugin '%s', skipping substitution",
                       plugin_name)
    else:
        logger.error("there is no configuration for plugin '%s'",
                     plugin_name)
        raise RuntimeError("plugin '%s' was specified multiple (%d) times, can't pick one",
                           plugin_name, plugins_num)


def get_checksums(path, algorithms):
    """
    Compute a checksum(s) of given file using specified algorithms.

    :param path: path to file
    :param algorithms: list of cryptographic hash functions, currently supported: md5, sha256
    :return: dictionary
    """
    if not algorithms:
        return {}

    compute_md5 = 'md5' in algorithms
    compute_sha256 = 'sha256' in algorithms

    if compute_md5:
        md5 = hashlib.md5()
    if compute_sha256:
        sha256 = hashlib.sha256()
    blocksize = 65536
    with open(path, mode='rb') as f:
        buf = f.read(blocksize)
        while len(buf) > 0:
            if compute_md5:
                md5.update(buf)
            if compute_sha256:
                sha256.update(buf)
            buf = f.read(blocksize)

    checksums = {}
    if compute_md5:
        checksums['md5sum'] = md5.hexdigest()
        logger.debug('md5sum: %s', checksums['md5sum'])
    if compute_sha256:
        checksums['sha256sum'] = sha256.hexdigest()
        logger.debug('sha256sum: %s', checksums['sha256sum'])
    return checksums


def get_docker_architecture(tasker):
    docker_version = tasker.get_version()
    host_arch = docker_version['Arch']
    if host_arch == 'amd64':
        host_arch = 'x86_64'
    return (host_arch, docker_version['Version'])


def get_exported_image_metadata(path, image_type):
    logger.info('getting metadata for exported image %s (%s)', path, image_type)
    metadata = {'path': path, 'type': image_type}
    if image_type != IMAGE_TYPE_OCI:
        metadata['size'] = os.path.getsize(path)
        logger.debug('size: %d bytes', metadata['size'])
        metadata.update(get_checksums(path, ['md5', 'sha256']))
    return metadata


def get_version_of_tools():
    """
    get versions of tools reactor is using (specified in constants.TOOLS_USED)

    :returns list of dicts, [{"name": "docker-py", "version": "1.2.3"}, ...]
    """
    response = []
    for tool in TOOLS_USED:
        pkg_name = tool["pkg_name"]
        try:
            tool_module = import_module(pkg_name)
        except ImportError as ex:
            logger.warning("can't import module %s: %r", pkg_name, ex)
        else:
            version = getattr(tool_module, "__version__", None)
            if version is None:
                logger.warning("tool %s doesn't have __version__", pkg_name)
            else:
                response.append({
                    "name": tool.get("display_name", pkg_name),
                    "version": version,
                    "path": tool_module.__file__,
                })
    return response


def print_version_of_tools():
    """
    print versions of used tools to logger
    """
    logger.info("Using these tools:")
    for tool in get_version_of_tools():
        logger.info("%s-%s at %s", tool["name"], tool["version"], tool["path"])


# each tuple is sorted from most preferred to least
_PREFERRED_LABELS = (
    ('name', 'Name'),
    ('version', 'Version'),
    ('release', 'Release'),
    ('architecture', 'Architecture'),
    ('vendor', 'Vendor'),
    ('authoritative-source', 'Authoritative_Registry'),
    ('com.redhat.component', 'BZComponent'),
    ('com.redhat.build-host', 'Build_Host'),
)


def get_all_label_keys(name):
    """
    Return the preference chain for the naming of a particular label.

    :param name: string, label name to search for
    :return: tuple, label names, most preferred first
    """

    for label_chain in _PREFERRED_LABELS:
        if name in label_chain:
            return label_chain
    else:
        # no variants known, return the name unchanged
        return (name,)


def get_preferred_label_key(labels, name):
    """
    We can have multiple variants of some labels (e.g. Version and version), sorted by preference.
    This function returns the best label corresponding to "name" that is present in the "labels"
    dictionary.

    Returns unchanged name if we don't have it in the preference table. If name is in the table
    but none of the variants are in the labels dict, returns the most-preferred label - the
    assumption is that we're gonna raise an error later and the error message should contain
    the preferred variant.
    """
    label_chain = get_all_label_keys(name)
    for lbl in label_chain:
        if lbl in labels:
            return lbl

    # none of the variants is in 'labels', return the best
    return label_chain[0]


def get_preferred_label(labels, name):
    key = get_preferred_label_key(labels, name)
    return labels.get(key)


def get_build_json():
    try:
        return json.loads(os.environ["BUILD"])
    except KeyError:
        logger.error("No $BUILD env variable. Probably not running in build container")
        raise


def is_scratch_build():
    build_json = get_build_json()
    try:
        return build_json['metadata']['labels'].get('scratch', False)
    except KeyError:
        logger.error('metadata.labels not found in build json')
        raise


# copypasted and slightly modified from
# http://stackoverflow.com/questions/1094841/reusable-library-to-get-human-readable-version-of-file-size/1094933#1094933
def human_size(num, suffix='B'):
    for unit in ['', 'Ki', 'Mi', 'Gi', 'Ti', 'Pi', 'Ei', 'Zi']:
        if abs(num) < 1024.0:
            return "%3.2f %s%s" % (num, unit, suffix)
        num /= 1024.0
    return "%.2f %s%s" % (num, 'Yi', suffix)


def registry_hostname(registry):
    """
    Strip a reference to a registry to just the hostname:port
    """
    if registry.startswith('http:') or registry.startswith('https:'):
        return urlparse(registry).netloc
    else:
        return registry


class Dockercfg(object):
    def __init__(self, secret_path):
        """
        Create a new Dockercfg object from a .dockercfg file whose
        containing directory is secret_path.

        :param secret_path: str, dirname of .dockercfg location
        """

        self.json_secret_path = os.path.join(secret_path, '.dockercfg')
        try:
            with open(self.json_secret_path) as fp:
                self.json_secret = json.load(fp)
        except Exception:
            msg = "failed to read registry secret"
            logger.error(msg, exc_info=True)
            raise RuntimeError(msg)

    def get_credentials(self, docker_registry):
        # For maximal robustness we check the host:port of the passed in
        # registry against the host:port of the items in the secret. This is
        # somewhat similar to what the Docker CLI does.
        #
        docker_registry = registry_hostname(docker_registry)
        try:
            return self.json_secret[docker_registry]
        except KeyError:
            for reg, creds in self.json_secret.items():
                if registry_hostname(reg) == docker_registry:
                    return creds

            logger.warn('%s not found in .dockercfg', docker_registry)
            return {}


class RegistrySession(object):
    def __init__(self, registry, insecure=False, dockercfg_path=None):
        self.registry = registry
        self._resolved = None
        self.insecure = insecure

        self.auth = None
        if dockercfg_path:
            dockercfg = Dockercfg(dockercfg_path).get_credentials(registry)

            username = dockercfg.get('username')
            password = dockercfg.get('password')
            if username and password:
                self.auth = requests.auth.HTTPBasicAuth(username, password)

        self._fallback = None
        if re.match('http(s)?://', self.registry):
            self._base = self.registry
        else:
            self._base = 'https://{}'.format(self.registry)
            if insecure:
                # In the insecure case, if the registry is just a hostname:port, we
                # don't know whether to talk HTTPS or HTTP to it, so we try first
                # with https then fallback
                self._fallback = 'http://{}'.format(self.registry)

        self.session = get_retrying_requests_session()

    def _do(self, f, relative_url, *args, **kwargs):
        kwargs['auth'] = self.auth
        kwargs['verify'] = not self.insecure
        if self._fallback:
            try:
                res = f(self._base + relative_url, *args, **kwargs)
                self._fallback = None  # don't fallback after one success
                return res
            except (SSLError, ConnectionError):
                self._base = self._fallback
                self._fallback = None
        return f(self._base + relative_url, *args, **kwargs)

    def get(self, relative_url, data=None, **kwargs):
        return self._do(self.session.get, relative_url, **kwargs)

    def head(self, relative_url, data=None, **kwargs):
        return self._do(self.session.head, relative_url, **kwargs)

    def post(self, relative_url, data=None, **kwargs):
        return self._do(self.session.post, relative_url, data=data, **kwargs)

    def put(self, relative_url, data=None, **kwargs):
        return self._do(self.session.put, relative_url, data=data, **kwargs)

    def delete(self, relative_url, **kwargs):
        return self._do(self.session.delete, relative_url, **kwargs)


class ManifestDigest(object):
    """Wrapper for digests for a docker manifest."""

    def __init__(self, v1=None, v2=None, v2_list=None, oci=None, oci_index=None):
        self.v1 = v1
        self.v2 = v2
        self.v2_list = v2_list
        self.oci = oci
        self.oci_index = oci_index

    @property
    def default(self):
        """Return the default manifest schema version.

        Depending on the docker version, <= 1.9, used to push
        the image to the registry, v2 schema may not be available.
        In such case, the v1 schema should be used when interacting
        with the registry. An OCI digest will only be present when
        the manifest was pushed as an OCI digest.
        """
        return self.v2_list or self.oci_index or self.oci or self.v2 or self.v1


def get_manifest_media_type(version):
    if version in ('v1', 'v2'):
        return 'application/vnd.docker.distribution.manifest.{}+json'.format(version)
    elif version == 'v2_list':
        return 'application/vnd.docker.distribution.manifest.list.v2+json'
    elif version == 'oci':
        return 'application/vnd.oci.image.manifest.v1+json'
    elif version == 'oci_index':
        return 'application/vnd.oci.image.index.v1+json'
    else:
        raise RuntimeError("Unknown manifest schema type")


def query_registry(registry_session, image, digest=None, version='v1', is_blob=False):
    """Return manifest digest for image.

    :param registry_session: RegistrySession
    :param image: ImageName, the remote image to inspect
    :param digest: str, digest of the image manifest
    :param version: str, which manifest schema version to fetch digest
    :param is_blob: bool, read blob config if set to True

    :return: requests.Response object
    """

    context = '/'.join([x for x in [image.namespace, image.repo] if x])
    reference = digest or image.tag or 'latest'
    object_type = 'manifests'
    if is_blob:
        object_type = 'blobs'

    headers = {'Accept': (get_manifest_media_type(version))}
    url = '/v2/{}/{}/{}'.format(context, object_type, reference)
    logger.debug("query_registry: querying {}, headers: {}".format(url, headers))

    response = registry_session.get(url, headers=headers)
    response.raise_for_status()

    return response


def get_manifest_digests(image, registry, insecure=False, dockercfg_path=None,
                         versions=('v1', 'v2', 'v2_list', 'oci', 'oci_index'), require_digest=True):
    """Return manifest digest for image.

    :param image: ImageName, the remote image to inspect
    :param registry: str, URI for registry, if URI schema is not provided,
                          https:// will be used
    :param insecure: bool, when True registry's cert is not verified
    :param dockercfg_path: str, dirname of .dockercfg location
    :param versions: tuple, which manifest schema versions to fetch digest
    :param require_digest: bool, when True exception is thrown if no digest is
                                 set in the headers.

    :return: dict, versions mapped to their digest
    """

    registry_session = RegistrySession(registry, insecure=insecure, dockercfg_path=dockercfg_path)

    digests = {}
    # If all of the media types return a 404 NOT_FOUND status, then we rethrow
    # an exception, if all of the media types fail for some other reason - like
    # bad headers - then we return a ManifestDigest object with no digests.
    # This is interesting for the Pulp "retry until the manifest shows up" case.
    all_not_found = True
    saved_not_found = None
    for version in versions:
        media_type = get_manifest_media_type(version)
        headers = {'Accept': media_type}

        try:
            response = query_registry(
                registry_session, image, digest=None,
                version=version)
            all_not_found = False
        except (HTTPError, RetryError, Timeout) as ex:
            if ex.response.status_code == requests.codes.not_found:
                saved_not_found = ex
            else:
                all_not_found = False

            # If the registry has a v2 manifest that can't be converted into a v1
            # manifest, the registry fails with status=400 (BAD_REQUEST), and an error code of
            # MANIFEST_INVALID. Note that if the registry has v2 manifest and
            # you ask for an OCI manifest, the registry will try to convert the
            # v2 manifest into a v1 manifest as the default type, so the same
            # thing occurs.
            if version != 'v2' and ex.response.status_code == requests.codes.bad_request:
                logger.warning('Unable to fetch digest for %s, got error %s',
                               media_type, ex.response.status_code)
                continue
            # Returned if the manifest could not be retrieved for the given
            # media type
            elif (ex.response.status_code == requests.codes.not_found or
                  ex.response.status_code == requests.codes.not_acceptable):
                continue
            else:
                raise

        received_media_type = None
        try:
            received_media_type = response.headers['Content-Type']
        except KeyError:
            # Guess content_type from contents
            try:
                encoding = guess_json_utf(response.content)
                manifest = json.loads(response.content.decode(encoding))
                received_media_type = manifest['mediaType']
            except (ValueError,  # not valid JSON
                    KeyError) as ex:  # no mediaType key
                logger.warning("Unable to fetch media type: neither Content-Type header "
                               "nor mediaType in output was found")

        if not received_media_type:
            continue

        # Only compare prefix as response may use +prettyjws suffix
        # which is the case for signed manifest
        response_h_prefix = received_media_type.rsplit('+', 1)[0]
        request_h_prefix = media_type.rsplit('+', 1)[0]
        if response_h_prefix != request_h_prefix:
            logger.debug('request headers: %s', headers)
            logger.debug('response headers: %s', response.headers)
            logger.warning('Received media type %s mismatches the expected %s',
                           received_media_type, media_type)
            continue

        # set it to truthy value so that koji_import would know pulp supports these digests
        digests[version] = True
        logger.debug('Received media type %s', received_media_type)

        if not response.headers.get('Docker-Content-Digest'):
            logger.warning('Unable to fetch digest for %s, no Docker-Content-Digest header',
                           media_type)
            continue

        digests[version] = response.headers['Docker-Content-Digest']
        context = '/'.join([x for x in [image.namespace, image.repo] if x])
        tag = image.tag or 'latest'
        logger.debug('Image %s:%s has %s manifest digest: %s',
                     context, tag, version, digests[version])

    if not digests:
        if all_not_found and len(versions) > 0:
            raise saved_not_found
        if require_digest:
            raise RuntimeError('No digests found for {}'.format(image))

    return ManifestDigest(**digests)


def get_config_from_registry(image, registry, digest, insecure=False,
                             dockercfg_path=None, version='v2'):
    """Return image config by digest

    :param image: ImageName, the remote image to inspect
    :param registry: str, URI for registry, if URI schema is not provided,
                          https:// will be used
    :param digest: str, digest of the image manifest
    :param insecure: bool, when True registry's cert is not verified
    :param dockercfg_path: str, dirname of .dockercfg location
    :param version: str, which manifest schema versions to fetch digest

    :return: dict, versions mapped to their digest
    """
    registry_session = RegistrySession(registry, insecure=insecure, dockercfg_path=dockercfg_path)

    response = query_registry(
        registry_session, image, digest=digest, version=version)
    response.raise_for_status()
    manifest_config = response.json()
    config_digest = manifest_config['config']['digest']

    config_response = query_registry(
        registry_session, image, digest=config_digest, version=version, is_blob=True)
    config_response.raise_for_status()

    blob_config = config_response.json()

    context = '/'.join([x for x in [image.namespace, image.repo] if x])
    tag = image.tag or 'latest'
    logger.debug('Image %s:%s has config:\n%s', context, tag, blob_config)

    return blob_config


def df_parser(df_path, workflow=None, cache_content=False, env_replace=True, parent_env=None):
    """
    Wrapper for dockerfile_parse's DockerfileParser that takes into account
    parent_env inheritance.

    :param df_path: string, path to Dockerfile (normally in DockerBuildWorkflow instance)
    :param workflow: DockerBuildWorkflow object instance, used to find parent image information
    :param cache_content: bool, tells DockerfileParser to cache Dockerfile content
    :param env_replace: bool, replace ENV declarations as part of DockerfileParser evaluation
    :param parent_env: dict, parent ENV key:value pairs to be inherited

    :return: DockerfileParser object instance
    """

    p_env = {}

    if parent_env:
        # If parent_env passed in, just use that
        p_env = parent_env

    elif workflow:

        # If parent_env is not provided, but workflow is then attempt to inspect
        # the workflow for the parent_env

        try:
            parent_config = workflow.base_image_inspect[INSPECT_CONFIG]
        except (AttributeError, TypeError, KeyError):
            logger.debug("base image unable to be inspected")
        else:
            try:
                tmp_env = parent_config["Env"]
                logger.debug("Parent Config ENV: %s" % tmp_env)

                if isinstance(tmp_env, dict):
                    p_env = tmp_env
                elif isinstance(tmp_env, list):
                    try:
                        for key_val in tmp_env:
                            key, val = key_val.split("=", 1)
                            p_env[key] = val

                    except ValueError:
                        logger.debug("Unable to parse all of Parent Config ENV")

            except KeyError:
                logger.debug("Parent Environment not found, not applied to Dockerfile")

    try:
        dfparser = DockerfileParser(
            df_path,
            cache_content=cache_content,
            env_replace=env_replace,
            parent_env=p_env
        )
    except TypeError:
        logger.debug("Old version of dockerfile-parse detected, unable to set inherited parent "
                     "ENVs")
        dfparser = DockerfileParser(
            df_path,
            cache_content=cache_content,
            env_replace=env_replace,
        )

    return dfparser


def are_plugins_in_order(plugins_conf, *plugins_names):
    """Check if plugins are configured in given order."""
    all_plugins_names = [plugin['name'] for plugin in plugins_conf or []]
    start_index = 0
    for plugin_name in plugins_names:
        try:
            start_index = all_plugins_names.index(plugin_name, start_index)
        except ValueError:
            return False
    return True


def read_yaml(yaml_file_path, schema):
    with open(yaml_file_path) as f:
        data = yaml.safe_load(f)

    try:
        resource = resource_stream('atomic_reactor', schema)
        schema = codecs.getreader('utf-8')(resource)
    except (IOError, TypeError):
        logger.error('unable to extract JSON schema, cannot validate')
        raise

    try:
        schema = json.load(schema)
    except ValueError:
        logger.error('unable to decode JSON schema, cannot validate')
        raise

    validator = jsonschema.Draft4Validator(schema=schema)
    try:
        jsonschema.Draft4Validator.check_schema(schema)
        validator.validate(data)
    except jsonschema.SchemaError:
        logger.error('invalid schema, cannot validate')
        raise
    except jsonschema.ValidationError:
        for error in validator.iter_errors(data):
            path = ''
            for element in error.absolute_path:
                if isinstance(element, int):
                    path += '[{}]'.format(element)
                else:
                    path += '.{}'.format(element)

            if path.startswith('.'):
                path = path[1:]

            logger.error('validation error (%s): %s', path or 'at top level', error.message)

        raise

    return data


class LabelFormatter(string.Formatter):
    """
    using this because str.format can't handle keys with dots and dashes
    which are included in some of the labels, such as
    'authoritative-source-url', 'com.redhat.component', etc
    """
    def get_field(self, field_name, args, kwargs):
        return (self.get_value(field_name, args, kwargs), field_name)


class SessionWithTimeout(requests.Session):
    """
    requests Session with added timeout
    """
    def __init__(self, *args, **kwargs):
        super(SessionWithTimeout, self).__init__(*args, **kwargs)

    def request(self, *args, **kwargs):
        kwargs.setdefault('timeout', HTTP_REQUEST_TIMEOUT)
        return super(SessionWithTimeout, self).request(*args, **kwargs)


def get_retrying_requests_session(client_statuses=HTTP_CLIENT_STATUS_RETRY,
                                  times=HTTP_MAX_RETRIES, delay=HTTP_BACKOFF_FACTOR,
                                  method_whitelist=None):
    retry = Retry(
        total=int(times),
        backoff_factor=delay,
        status_forcelist=client_statuses,
        method_whitelist=method_whitelist
    )
    session = SessionWithTimeout()
    session.mount('http://', HTTPAdapter(max_retries=retry))
    session.mount('https://', HTTPAdapter(max_retries=retry))

    return session


def get_primary_images(workflow):
    primary_images = workflow.tag_conf.primary_images
    if not primary_images:
        primary_images = [
            ImageName.parse(primary) for primary in
            workflow.build_result.annotations['repositories']['primary']]
    return primary_images
