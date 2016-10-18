"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.


Script for building docker image. This is expected to run inside container.
"""

import json
import logging
import tempfile
import datetime

from atomic_reactor.build import InsideBuilder
from atomic_reactor.plugin import PostBuildPluginsRunner, PreBuildPluginsRunner, InputPluginsRunner, PrePublishPluginsRunner, \
    ExitPluginsRunner, PluginFailedException, AutoRebuildCanceledException
from atomic_reactor.source import get_source_instance_for
from atomic_reactor.util import ImageName

# this import is required for mypy to work correctly
try:
    from typing import Any
    from atomic_reactor.build import BuildResult
except:
    pass


logger = logging.getLogger(__name__)


class BuildResults(object):
    build_logs = None # type: List[str]
    dockerfile = None # type: str
    built_img_inspect = None # type: str
    built_img_info = None # type: str
    base_img_inspect = None # type: str
    base_img_info = None # type: str
    base_plugins_output = None # type: str
    built_img_plugins_output = None # type: str
    container_id = None # type: str
    return_code = None # type: int


class BuildResultsEncoder(json.JSONEncoder):
    def default(self, obj):
        # type: (BuildResults) -> Dict[str, object]
        if isinstance(obj, BuildResults):
            return {
                'build_logs': obj.build_logs,
                'built_img_inspect': obj.built_img_inspect,
                'built_img_info': obj.built_img_info,
                'base_img_info': obj.base_img_info,
                'base_plugins_output': obj.base_plugins_output,
                'built_img_plugins_output': obj.built_img_plugins_output,
            }
        # Let the base class default method raise the TypeError
        return json.JSONEncoder.default(self, obj)


class BuildResultsJSONDecoder(json.JSONDecoder):
    def decode(self, obj):
        d = super(BuildResultsJSONDecoder, self).decode(obj)
        results = BuildResults()
        results.built_img_inspect = d.get('built_img_inspect', None)
        results.built_img_info = d.get('built_img_info', None)
        results.base_img_info = d.get('base_img_info', None)
        results.base_plugins_output = d.get('base_plugins_output', None)
        results.built_img_plugins_output = d.get('built_img_plugins_output', None)
        return results


class TagConf(object):
    """
    confguration of image names and tags to be applied
    """

    def __init__(self):
        # type: () -> None
        # list of ImageNames with predictable names
        self._primary_images = []  # type: List[str]
        # list if ImageName instances with unpredictable names
        self._unique_images = []  # type: List[str]

    @property
    def primary_images(self):  # type: () -> List[str]
        """
        primary image names are predictable and should be used for layering

        this is consumed by metadata plugin

        :return: list of ImageName
        """
        return self._primary_images

    @property
    def images(self):  # type: () -> List[str]
        """
        list of all ImageNames

        :return: list of ImageName
        """
        return self._primary_images + self._unique_images

    @property
    def unique_images(self):  # type: () -> List[str]
        """
        unique image names are unpredictable and should be used for tracking only

        this is consumed by metadata plugin

        :return: list of ImageName
        """
        return self._unique_images

    def add_primary_image(self, image):  # type: (str) -> None
        """
        add new primary image

        used by tag_by_labels plugin

        :param image: str, name of image (e.g. "namespace/httpd:2.4")
        :return: None
        """
        self._primary_images.append(ImageName.parse(image))

    def add_unique_image(self, image):  # type: (str) -> None
        """
        add image with unpredictable name

        used by tag_by_labels plugin

        :param image: str, name of image (e.g. "namespace/httpd:2.4")
        :return: None
        """
        self._unique_images.append(ImageName.parse(image))

    def add_primary_images(self, images):  # type: (str) -> None
        """
        add new primary images in bulk

        used by tag_by_labels plugin

        :param images: list of str, list of image names
        :return: None
        """
        for image in images:
            self.add_primary_image(image)


class Registry(object):
    def __init__(self, uri, insecure=False):  # type: (str, bool) -> None
        """
        abstraction for all registry classes

        :param uri: str, uri for pulling (in case of docker-registry, pushing too)
        :param insecure: bool
        """
        self.uri = uri
        self.insecure = insecure


class PulpRegistry(Registry):
    """ pulp & crane """
    def __init__(self, name, crane_uri, insecure=False):  # type: (str, str, bool) -> None
        """
        :param name: str, pulp's rest api is specified in dockpulp's config, we refer only by name
        :param crane_uri: str, read-only docker registry api access point
        :param insecure: bool
        """
        super(PulpRegistry, self).__init__(crane_uri, insecure=insecure)
        self.name = name  # type: str


class DockerRegistry(Registry):
    """ v1/v2 docker registry """
    def __init__(self, uri, insecure=False):  # type: (str, bool) -> None
        """
        :param uri: str, uri for pushing/pulling
        :param insecure: bool
        """
        super(DockerRegistry, self).__init__(uri, insecure=insecure)
        # maps tags (str) to their digest, if available
        self.digests = {}  # type: Dict[str, str]
        # stores image config from the registry
        self.config = None  # type: Dict[str, Any]
        # media type of the config is application/vnd.docker.container.image.v1+json


# TODO: annotate this, as currently it throws all kinds of errors in mypy
class PushConf(object):
    """
    configuration of remote registries: docker-registry or pulp
    """

    def __init__(self):
        self._registries = {
            "docker": [],
            "pulp": {},  # name -> PulpRegistry
        }

    def add_docker_registry(self, registry_uri, insecure=False):
        if registry_uri is None:
            raise RuntimeError("registry URI cannot be None")
        r = DockerRegistry(registry_uri, insecure=insecure)
        # mypy throws 'error: "object" has no attribute "append"', don't know why
        self._registries["docker"].append(r)
        return r

    def add_docker_registries(self, registry_uris, insecure=False):
        for registry_uri in registry_uris:
            self.add_docker_registry(registry_uri, insecure=insecure)

    def add_pulp_registry(self, name, crane_uri):
        if crane_uri is None:
            raise RuntimeError("registry URI cannot be None")
        r = PulpRegistry(name, crane_uri)
        self._registries["pulp"][name] = r
        return r

    @property
    def has_some_docker_registry(self):
        return len(self.docker_registries) > 0

    @property
    def docker_registries(self):
        return self._registries["docker"]  # type: ignore

    @property
    def pulp_registries(self):
        return [registry for registry in self._registries["pulp"].values()]

    @property
    def all_registries(self):  # type: () -> List[object]
        return self.docker_registries + self.pulp_registries


class DockerBuildWorkflow(object):
    """
    This class defines a workflow for building images:

    1. pull image from registry
    2. tag it properly if needed
    3. obtain source
    4. build image
    5. tag it
    6. push it to registries
    """

    def __init__(self, source, image, prebuild_plugins=None, prepublish_plugins=None,
                 postbuild_plugins=None, exit_plugins=None, plugin_files=None,
                 openshift_build_selflink=None, **kwargs):
        # type: (Dict[str, str], str, Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any], List[str], str, **Any) -> None
        """
        :param source: dict, where/how to get source code to put in image
        :param image: str, tag for built image ([registry/]image_name[:tag])
        :param prebuild_plugins: dict, arguments for pre-build plugins
        :param prepublish_plugins: dict, arguments for test-build plugins
        :param postbuild_plugins: dict, arguments for post-build plugins
        :param plugin_files: list of str, load plugins also from these files
        :param openshift_build_selflink: str, link to openshift build (if we're actually running
            on openshift) without the actual hostname/IP address
        """
        self.source = get_source_instance_for(source, tmpdir=tempfile.mkdtemp())
        self.image = image

        self.prebuild_plugins_conf = prebuild_plugins
        self.prepublish_plugins_conf = prepublish_plugins
        self.postbuild_plugins_conf = postbuild_plugins
        self.exit_plugins_conf = exit_plugins
        self.prebuild_results = {}  # type: Dict[str, Any]
        self.postbuild_results = {}  # type: Dict[str, Any]
        self.prepub_results = {}  # type: Dict[str, Any]
        self.exit_results = {}  # type: Dict[str, Any]
        self.plugin_workspace = {}  # type: Dict[str, Any]
        self.plugins_timestamps = {}  # type: Dict[str, Any]
        self.plugins_durations = {}  # type: Dict[str, Any]
        self.plugins_errors = {}  # type: Dict[str, Any]
        self.autorebuild_canceled = False
        self.build_failed = False
        self.plugin_failed = False  # type: bool
        self.plugin_files = plugin_files

        self.kwargs = kwargs

        self.builder = None  # type: InsideBuilder
        self.build_logs = []  # type: List[str]
        self.built_image_inspect = None  # type: Dict[str, Any]
        self._base_image_inspect = None  # type: Dict[str, Any]

        self.pulled_base_images = set()  # type: Set[str]

        # When an image is exported into tarball, it can then be processed by various plugins.
        #  Each plugin that transforms the image should save it as a new file and append it to
        #  the end of exported_image_sequence. Other plugins should then operate with last
        #  member of this structure. Example:
        #  [{'path': '/tmp/foo.tar', 'size': 12345678, 'md5sum': '<md5>', 'sha256sum': '<sha256>'}]
        #  You can use util.get_exported_image_metadata to create a dict to append to this list.
        self.exported_image_sequence = []  # type: List[Dict[str, Any]]

        self.tag_conf = TagConf()
        self.push_conf = PushConf()

        # mapping of downloaded files; DON'T PUT ANYTHING BIG HERE!
        # "path/to/file" -> "content"
        self.files = {}  # type: Dict[str, Any]

        self.openshift_build_selflink = openshift_build_selflink

        if kwargs:
            logger.warning("unprocessed keyword arguments: %s", kwargs)

    @property
    def build_process_failed(self):  # type: () -> bool
        """
        Has any aspect of the build process failed?
        """
        return self.build_failed or self.plugin_failed

    # inspect base image lazily just before it's needed - pre plugins may change the base image
    @property
    def base_image_inspect(self):  # type: () -> Dict[str, Any]
        if self._base_image_inspect is None:
            self._base_image_inspect = self.builder.tasker.inspect_image(self.builder.base_image)
        return self._base_image_inspect

    def build_docker_image(self):  # type: () -> BuildResult
        """
        build docker image

        :return: BuildResult
        """
        self.builder = InsideBuilder(self.source, self.image)
        try:
            # time to run pre-build plugins, so they can access cloned repo
            logger.info("running pre-build plugins")
            prebuild_runner = PreBuildPluginsRunner(self.builder.tasker, self, self.prebuild_plugins_conf,
                                                    plugin_files=self.plugin_files)
            try:
                prebuild_runner.run()
            except PluginFailedException as ex:
                logger.error("one or more prebuild plugins failed: %s", ex)
                raise
            except AutoRebuildCanceledException as ex:
                logger.info(str(ex))
                self.autorebuild_canceled = True
                raise

            start_time = datetime.datetime.now()
            self.plugins_timestamps['dockerbuild'] = start_time.isoformat()

            build_result = self.builder.build()

            try:
                finish_time = datetime.datetime.now()
                duration = finish_time - start_time
                seconds = duration.total_seconds()
                logger.debug("build finished in %ds", seconds)
                self.plugins_durations['dockerbuild'] = seconds
            except Exception:
                logger.exception("failed to save build duration")

            self.build_logs = build_result.logs

            self.build_failed = build_result.is_failed()

            if build_result.is_failed():
                # The docker build failed. Finish here, just run the
                # exit plugins (from the 'finally:' block below).
                self.plugins_errors['dockerbuild'] = ''
                return build_result

            self.built_image_inspect = self.builder.inspect_built_image()

            # run prepublish plugins
            prepublish_runner = PrePublishPluginsRunner(self.builder.tasker, self, self.prepublish_plugins_conf,
                                                        plugin_files=self.plugin_files)
            try:
                prepublish_runner.run()
            except PluginFailedException as ex:
                logger.error("one or more prepublish plugins failed: %s", ex)
                raise

            postbuild_runner = PostBuildPluginsRunner(self.builder.tasker, self, self.postbuild_plugins_conf,
                                                      plugin_files=self.plugin_files)
            try:
                postbuild_runner.run()
            except PluginFailedException as ex:
                logger.error("one or more postbuild plugins failed: %s", ex)
                raise

            return build_result
        finally:
            exit_runner = ExitPluginsRunner(self.builder.tasker, self,
                                            self.exit_plugins_conf,
                                            plugin_files=self.plugin_files)
            try:
                exit_runner.run(keep_going=True)
            except PluginFailedException as ex:
                logger.error("one or more exit plugins failed: %s", ex)
                raise
            finally:
                self.source.remove_tmpdir()


def build_inside(input_method, input_args=None, substitutions=None):
    # type: (str, List[str], List[str]) -> None
    """
    use requested input plugin to load configuration and then initiate build
    """
    def process_keyvals(keyvals):
        """ ["key=val", "x=y"] -> {"key": "val", "x": "y"} """
        keyvals = keyvals or []
        processed_keyvals = {}
        for arg in keyvals:
            key, value = arg.split("=", 1)
            processed_keyvals[key] = value
        return processed_keyvals

    if not input_method:
        raise RuntimeError("No input method specified!")
    else:
        logger.debug("getting build json from input %s", input_method)

        cleaned_input_args = process_keyvals(input_args)
        cleaned_subs = process_keyvals(substitutions)

        cleaned_input_args['substitutions'] = cleaned_subs

        input_runner = InputPluginsRunner([{'name': input_method,
                                            'args': cleaned_input_args}])
        build_json = input_runner.run()[input_method]
        logger.debug("build json: %s", build_json)
    if not build_json:
        raise RuntimeError("No valid build json!")
    # TODO: validate json
    dbw = DockerBuildWorkflow(**build_json)
    build_result = dbw.build_docker_image()
    if not build_result or build_result.is_failed():
        raise RuntimeError("no image built")
    else:
        logger.info("build has finished successfully \o/")
