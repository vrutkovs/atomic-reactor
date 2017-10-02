"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""

from __future__ import unicode_literals

import json
import os
import random
from string import ascii_letters
import subprocess
from tempfile import NamedTemporaryFile
import time
import copy

from atomic_reactor import __version__ as atomic_reactor_version
from atomic_reactor import start_time as atomic_reactor_start_time
from atomic_reactor.plugin import ExitPlugin
from atomic_reactor.source import GitSource
from atomic_reactor.plugins.post_rpmqa import PostBuildRPMqaPlugin
from atomic_reactor.plugins.pre_add_filesystem import AddFilesystemPlugin
from atomic_reactor.plugins.pre_check_and_set_rebuild import is_rebuild
from atomic_reactor.plugins.pre_flatpak_create_dockerfile import get_flatpak_source_info
from atomic_reactor.plugins.pre_add_help import AddHelpPlugin
try:
    from atomic_reactor.plugins.post_pulp_sync import get_manifests_in_pulp_repository
except ImportError:
    # no dockpulp available
    def get_manifests_in_pulp_repository(_):
        raise KeyError

from atomic_reactor.constants import (PROG, PLUGIN_KOJI_PROMOTE_PLUGIN_KEY,
                                      PLUGIN_KOJI_TAG_BUILD_KEY,
                                      PLUGIN_PULP_PULL_KEY,
                                      PLUGIN_KOJI_PARENT_KEY)
from atomic_reactor.util import (get_version_of_tools, get_checksums,
                                 get_build_json, get_preferred_label,
                                 get_docker_architecture, df_parser,
                                 are_plugins_in_order,
                                 get_image_upload_filename)
from atomic_reactor.koji_util import (create_koji_session, tag_koji_build,
                                      Output, KojiUploadLogger)
from atomic_reactor.rpm_util import parse_rpm_output, rpm_qf_args
from osbs.conf import Configuration
from osbs.api import OSBS
from osbs.exceptions import OsbsException


class KojiPromotePlugin(ExitPlugin):
    """
    Promote this build to Koji

    Submits a successful build to Koji using the Content Generator API,
    https://fedoraproject.org/wiki/Koji/ContentGenerators

    Authentication is with Kerberos unless the koji_ssl_certs
    configuration parameter is given, in which case it should be a
    path at which 'cert', 'ca', and 'serverca' are the certificates
    for SSL authentication.

    If Kerberos is used for authentication, the default principal will
    be used (from the kernel keyring) unless both koji_keytab and
    koji_principal are specified. The koji_keytab parameter is a
    keytab name like 'type:name', and so can be used to specify a key
    in a Kubernetes secret by specifying 'FILE:/path/to/key'.

    If metadata_only is set, the 'docker save' image will not be
    uploaded, only the logs. The import will be marked as
    metadata-only.

    Runs as an exit plugin in order to capture logs from all other
    plugins.
    """

    key = PLUGIN_KOJI_PROMOTE_PLUGIN_KEY
    is_allowed_to_fail = False

    def __init__(self, tasker, workflow, kojihub, url,
                 verify_ssl=True, use_auth=True,
                 koji_ssl_certs=None, koji_proxy_user=None,
                 koji_principal=None, koji_keytab=None,
                 metadata_only=False, blocksize=None,
                 target=None, poll_interval=5):
        """
        constructor

        :param tasker: DockerTasker instance
        :param workflow: DockerBuildWorkflow instance
        :param kojihub: string, koji hub (xmlrpc)
        :param url: string, URL for OSv3 instance
        :param verify_ssl: bool, verify OSv3 SSL certificate?
        :param use_auth: bool, initiate authentication with OSv3?
        :param koji_ssl_certs: str, path to 'cert', 'ca', 'serverca'
        :param koji_proxy_user: str, user to log in as (requires hub config)
        :param koji_principal: str, Kerberos principal (must specify keytab)
        :param koji_keytab: str, keytab name (must specify principal)
        :param metadata_only: bool, whether to omit the 'docker save' image
        :param blocksize: int, blocksize to use for uploading files
        :param target: str, koji target
        :param poll_interval: int, seconds between Koji task status requests
        """
        super(KojiPromotePlugin, self).__init__(tasker, workflow)

        self.kojihub = kojihub
        self.koji_ssl_certs = koji_ssl_certs
        self.koji_proxy_user = koji_proxy_user

        self.koji_principal = koji_principal
        self.koji_keytab = koji_keytab

        self.metadata_only = metadata_only
        self.blocksize = blocksize
        self.target = target
        self.poll_interval = poll_interval

        self.namespace = get_build_json().get('metadata', {}).get('namespace', None)
        osbs_conf = Configuration(conf_file=None, openshift_uri=url,
                                  use_auth=use_auth, verify_ssl=verify_ssl,
                                  namespace=self.namespace)
        self.osbs = OSBS(osbs_conf, osbs_conf)
        self.build_id = None
        self.pullspec_image = None

    def get_rpms(self):
        """
        Build a list of installed RPMs in the format required for the
        metadata.
        """

        tags = [
            'NAME',
            'VERSION',
            'RELEASE',
            'ARCH',
            'EPOCH',
            'SIGMD5',
            'SIGPGP:pgpsig',
            'SIGGPG:pgpsig',
        ]

        cmd = "/bin/rpm " + rpm_qf_args(tags)
        try:
            # py3
            (status, output) = subprocess.getstatusoutput(cmd)
        except AttributeError:
            # py2
            with open('/dev/null', 'r+') as devnull:
                p = subprocess.Popen(cmd,
                                     shell=True,
                                     stdin=devnull,
                                     stdout=subprocess.PIPE,
                                     stderr=devnull)

                (stdout, stderr) = p.communicate()
                status = p.wait()
                output = stdout.decode()

        if status != 0:
            self.log.debug("%s: stderr output: %s", cmd, stderr)
            raise RuntimeError("%s: exit code %s" % (cmd, status))

        return parse_rpm_output(output.splitlines(), tags)

    def get_output_metadata(self, path, filename):
        """
        Describe a file by its metadata.

        :return: dict
        """

        checksums = get_checksums(path, ['md5'])
        metadata = {'filename': filename,
                    'filesize': os.path.getsize(path),
                    'checksum': checksums['md5sum'],
                    'checksum_type': 'md5'}

        if self.metadata_only:
            metadata['metadata_only'] = True

        return metadata

    def get_builder_image_id(self):
        """
        Find out the docker ID of the buildroot image we are in.
        """

        try:
            buildroot_tag = os.environ["OPENSHIFT_CUSTOM_BUILD_BASE_IMAGE"]
        except KeyError:
            return ''

        try:
            pod = self.osbs.get_pod_for_build(self.build_id)
            all_images = pod.get_container_image_ids()
        except OsbsException as ex:
            self.log.error("unable to find image id: %r", ex)
            return buildroot_tag

        try:
            return all_images[buildroot_tag]
        except KeyError:
            self.log.error("Unable to determine buildroot image ID for %s",
                           buildroot_tag)
            return buildroot_tag

    def get_buildroot(self, build_id):
        """
        Build the buildroot entry of the metadata.

        :return: dict, partial metadata
        """

        docker_info = self.tasker.get_info()
        host_arch, docker_version = get_docker_architecture(self.tasker)

        buildroot = {
            'id': 1,
            'host': {
                'os': docker_info['OperatingSystem'],
                'arch': host_arch,
            },
            'content_generator': {
                'name': PROG,
                'version': atomic_reactor_version,
            },
            'container': {
                'type': 'docker',
                'arch': os.uname()[4],
            },
            'tools': [
                {
                    'name': tool['name'],
                    'version': tool['version'],
                }
                for tool in get_version_of_tools()] + [
                {
                    'name': 'docker',
                    'version': docker_version,
                },
            ],
            'components': self.get_rpms(),
            'extra': {
                'osbs': {
                    'build_id': build_id,
                    'builder_image_id': self.get_builder_image_id(),
                }
            },
        }

        return buildroot

    def get_logs(self):
        """
        Build the logs entry for the metadata 'output' section

        :return: list, Output instances
        """

        output = []

        # Collect logs from server
        try:
            logs = self.osbs.get_build_logs(self.build_id)
        except OsbsException as ex:
            self.log.error("unable to get build logs: %r", ex)
        else:
            # Deleted once closed
            logfile = NamedTemporaryFile(prefix=self.build_id,
                                         suffix=".log",
                                         mode='wb')
            try:
                logfile.write(logs)
            except (TypeError, UnicodeEncodeError):
                # Older osbs-client versions returned Unicode objects
                logfile.write(logs.encode('utf-8'))
            logfile.flush()
            metadata = self.get_output_metadata(logfile.name,
                                                "openshift-final.log")
            output.append(Output(file=logfile, metadata=metadata))

        docker_logs = NamedTemporaryFile(prefix="docker-%s" % self.build_id,
                                         suffix=".log",
                                         mode='wb')
        docker_logs.write("\n".join(self.workflow.build_result.logs).encode('utf-8'))
        docker_logs.flush()
        output.append(Output(file=docker_logs,
                             metadata=self.get_output_metadata(docker_logs.name,
                                                               "build.log")))
        return output

    def get_image_components(self):
        """
        Re-package the output of the rpmqa plugin into the format required
        for the metadata.
        """

        output = self.workflow.image_components
        if output is None:
            self.log.error("%s plugin did not run!",
                           PostBuildRPMqaPlugin.key)
            output = []

        return output

    def get_image_output(self, arch):
        """
        Create the output for the image

        This is the Koji Content Generator metadata, along with the
        'docker save' output to upload.

        For metadata-only builds, an empty file is used instead of the
        output of 'docker save'.

        :param arch: str, architecture for this output
        :return: tuple, (metadata dict, Output instance)

        """

        saved_image = self.workflow.exported_image_sequence[-1].get('path')
        image_name = get_image_upload_filename(self.workflow.exported_image_sequence[-1],
                                               self.workflow.builder.image_id,
                                               arch)
        if self.metadata_only:
            metadata = self.get_output_metadata(os.path.devnull, image_name)
            output = Output(file=None, metadata=metadata)
        else:
            metadata = self.get_output_metadata(saved_image, image_name)
            output = Output(file=open(saved_image), metadata=metadata)

        return metadata, output

    def get_digests(self):
        """
        Returns a map of images to their digests
        """

        try:
            pulp = get_manifests_in_pulp_repository(self.workflow)
        except KeyError:
            pulp = None

        digests = {}  # repository -> digests
        for registry in self.workflow.push_conf.docker_registries:
            for image in self.workflow.tag_conf.images:
                image_str = image.to_str()
                if image_str in registry.digests:
                    image_digests = registry.digests[image_str]
                    if pulp is None:
                        digest_list = [image_digests.default]
                    else:
                        # If Pulp is enabled, only report digests that
                        # were synced into Pulp. This may not be all
                        # of them, depending on whether Pulp has
                        # schema 2 support.
                        digest_list = [digest for digest in (image_digests.v1,
                                                             image_digests.v2)
                                       if digest in pulp]

                    digests[image.to_str(registry=False)] = digest_list

        return digests

    def get_repositories(self, digests):
        """
        Build the repositories metadata

        :param digests: dict, image -> digests
        """
        if self.workflow.push_conf.pulp_registries:
            # If pulp was used, only report pulp images
            registries = self.workflow.push_conf.pulp_registries
        else:
            # Otherwise report all the images we pushed
            registries = self.workflow.push_conf.all_registries

        output_images = []
        for registry in registries:
            image = self.pullspec_image.copy()
            image.registry = registry.uri
            pullspec = image.to_str()

            output_images.append(pullspec)

            digest_list = digests.get(image.to_str(registry=False), ())
            for digest in digest_list:
                digest_pullspec = image.to_str(tag=False) + "@" + digest
                output_images.append(digest_pullspec)

        return output_images

    def get_output(self, buildroot_id):
        """
        Build the 'output' section of the metadata.

        :return: list, Output instances
        """

        def add_buildroot_id(output):
            logfile, metadata = output
            metadata.update({'buildroot_id': buildroot_id})
            return Output(file=logfile, metadata=metadata)

        def add_log_type(output):
            logfile, metadata = output
            metadata.update({'type': 'log', 'arch': 'noarch'})
            return Output(file=logfile, metadata=metadata)

        output_files = [add_log_type(add_buildroot_id(metadata))
                        for metadata in self.get_logs()]

        # Parent of squashed built image is base image
        image_id = self.workflow.builder.image_id
        parent_id = self.workflow.base_image_inspect['Id']

        # Read config from the registry using v2 schema 2 digest
        registries = self.workflow.push_conf.docker_registries
        if registries:
            config = copy.deepcopy(registries[0].config)
        else:
            config = {}

        # We don't need container_config section
        if config and 'container_config' in config:
            del config['container_config']

        digests = self.get_digests()
        repositories = self.get_repositories(digests)
        arch = os.uname()[4]
        tags = set(image.tag for image in self.workflow.tag_conf.primary_images)
        metadata, output = self.get_image_output(arch)
        metadata.update({
            'arch': arch,
            'type': 'docker-image',
            'components': self.get_image_components(),
            'extra': {
                'image': {
                    'arch': arch,
                },
                'docker': {
                    'id': image_id,
                    'parent_id': parent_id,
                    'repositories': repositories,
                    'layer_sizes': self.workflow.layer_sizes,
                    'tags': list(tags),
                    'config': config
                },
            },
        })

        if not config:
            del metadata['extra']['docker']['config']

        # Add the 'docker save' image to the output
        image = add_buildroot_id(output)
        output_files.append(image)

        return output_files

    def get_build(self, metadata):
        start_time = int(atomic_reactor_start_time)

        labels = df_parser(self.workflow.builder.df_path, workflow=self.workflow).labels

        component = get_preferred_label(labels, 'com.redhat.component')
        version = get_preferred_label(labels, 'version')
        release = get_preferred_label(labels, 'release')

        source = self.workflow.source
        if not isinstance(source, GitSource):
            raise RuntimeError('git source required')

        extra = {'image': {'autorebuild': is_rebuild(self.workflow)}}

        koji_task_id = metadata.get('labels', {}).get('koji-task-id')
        if koji_task_id is not None:
            self.log.info("build configuration created by Koji Task ID %s",
                          koji_task_id)
            try:
                extra['container_koji_task_id'] = int(koji_task_id)
            except ValueError:
                self.log.error("invalid task ID %r", koji_task_id, exc_info=1)

        fs_result = self.workflow.prebuild_results.get(AddFilesystemPlugin.key)
        if fs_result is not None:
            try:
                fs_task_id = fs_result['filesystem-koji-task-id']
            except KeyError:
                self.log.error("%s: expected filesystem-koji-task-id in result",
                               AddFilesystemPlugin.key)
            else:
                try:
                    task_id = int(fs_task_id)
                except ValueError:
                    self.log.error("invalid task ID %r", fs_task_id, exc_info=1)
                else:
                    extra['filesystem_koji_task_id'] = task_id

        # Append media_types from pulp pull
        pulp_pull_results = self.workflow.postbuild_results.get(PLUGIN_PULP_PULL_KEY)
        if pulp_pull_results:
            extra['image']['media_types'] = sorted(list(set(pulp_pull_results)))

        # Append parent_build_id from koji parent
        parent_results = self.workflow.prebuild_results.get(PLUGIN_KOJI_PARENT_KEY) or {}
        parent_id = parent_results.get('parent-image-koji-build-id')
        if parent_id is not None:
            try:
                parent_id = int(parent_id)
            except ValueError:
                self.log.exception("invalid koji parent id %r", parent_id)
            else:
                extra['image']['parent_build_id'] = parent_id

        help_result = self.workflow.prebuild_results.get(AddHelpPlugin.key)
        if isinstance(help_result, dict) and 'help_file' in help_result and 'status' in help_result:
            if help_result['status'] == AddHelpPlugin.NO_HELP_FILE_FOUND:
                extra['image']['help'] = None
            elif help_result['status'] == AddHelpPlugin.HELP_GENERATED:
                extra['image']['help'] = help_result['help_file']
            else:
                self.log.error("Unknown result from add_help plugin: %s", help_result)

        flatpak_source_info = get_flatpak_source_info(self.workflow)
        if flatpak_source_info is not None:
            extra['image'].update(flatpak_source_info.koji_metadata())

        build = {
            'name': component,
            'version': version,
            'release': release,
            'source': "{0}#{1}".format(source.uri, source.commit_id),
            'start_time': start_time,
            'end_time': int(time.time()),
            'extra': extra,
        }

        if self.metadata_only:
            build['metadata_only'] = True

        return build

    def get_metadata(self):
        """
        Build the metadata needed for importing the build

        :return: tuple, the metadata and the list of Output instances
        """
        try:
            metadata = get_build_json()["metadata"]
            self.build_id = metadata["name"]
        except KeyError:
            self.log.error("No build metadata")
            raise

        for image in self.workflow.tag_conf.unique_images:
            self.pullspec_image = image
            break

        for image in self.workflow.tag_conf.primary_images:
            # dash at first/last postition does not count
            if '-' in image.tag[1:-1]:
                self.pullspec_image = image
                break

        if not self.pullspec_image:
            raise RuntimeError('Unable to determine pullspec_image')

        metadata_version = 0

        build = self.get_build(metadata)
        buildroot = self.get_buildroot(build_id=self.build_id)
        output_files = self.get_output(buildroot['id'])

        koji_metadata = {
            'metadata_version': metadata_version,
            'build': build,
            'buildroots': [buildroot],
            'output': [output.metadata for output in output_files],
        }

        return koji_metadata, output_files

    def upload_file(self, session, output, serverdir):
        """
        Upload a file to koji

        :return: str, pathname on server
        """
        name = output.metadata['filename']
        self.log.debug("uploading %r to %r as %r",
                       output.file.name, serverdir, name)

        kwargs = {}
        if self.blocksize is not None:
            kwargs['blocksize'] = self.blocksize
            self.log.debug("using blocksize %d", self.blocksize)

        upload_logger = KojiUploadLogger(self.log)
        session.uploadWrapper(output.file.name, serverdir, name=name,
                              callback=upload_logger.callback, **kwargs)
        path = os.path.join(serverdir, name)
        self.log.debug("uploaded %r", path)
        return path

    @staticmethod
    def get_upload_server_dir():
        """
        Create a path name for uploading files to

        :return: str, path name expected to be unique
        """
        dir_prefix = 'koji-promote'
        random_chars = ''.join([random.choice(ascii_letters)
                                for _ in range(8)])
        unique_fragment = '%r.%s' % (time.time(), random_chars)
        return os.path.join(dir_prefix, unique_fragment)

    def login(self):
        """
        Log in to koji

        :return: koji.ClientSession instance, logged in
        """

        # krbV python library throws an error if these are unicode
        auth_info = {
            "proxyuser": self.koji_proxy_user,
            "ssl_certs_dir": self.koji_ssl_certs,
            "krb_principal": str(self.koji_principal),
            "krb_keytab": str(self.koji_keytab)
        }
        return create_koji_session(str(self.kojihub), auth_info)

    def run(self):
        """
        Run the plugin.
        """

        if ((self.koji_principal and not self.koji_keytab) or
                (self.koji_keytab and not self.koji_principal)):
            raise RuntimeError("specify both koji_principal and koji_keytab "
                               "or neither")

        # Only run if the build was successful
        if self.workflow.build_process_failed:
            self.log.info("Not promoting failed build to koji")
            return

        koji_metadata, output_files = self.get_metadata()

        try:
            session = self.login()
            server_dir = self.get_upload_server_dir()
            for output in output_files:
                if output.file:
                    self.upload_file(session, output, server_dir)
        finally:
            for output in output_files:
                if output.file:
                    output.file.close()

        try:
            build_info = session.CGImport(koji_metadata, server_dir)
        except Exception:
            self.log.debug("metadata: %r", koji_metadata)
            raise

        # Older versions of CGImport do not return a value.
        build_id = build_info.get("id") if build_info else None

        self.log.debug("Build information: %s",
                       json.dumps(build_info, sort_keys=True, indent=4))

        # If configured, koji_tag_build plugin will perform build tagging
        tag_later = are_plugins_in_order(self.workflow.exit_plugins_conf,
                                         PLUGIN_KOJI_PROMOTE_PLUGIN_KEY,
                                         PLUGIN_KOJI_TAG_BUILD_KEY)
        if not tag_later and build_id is not None and self.target is not None:
            tag_koji_build(session, build_id, self.target,
                           poll_interval=self.poll_interval)

        return build_id
