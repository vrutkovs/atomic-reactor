"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.


Classes which implement tasks which builder has to be capable of doing.
Logic above these classes has to set the workflow itself.
"""
import json

import logging
import traceback
from dockerfile_parse import DockerfileParser
from atomic_reactor.core import DockerTasker, LastLogger
from atomic_reactor.util import wait_for_command, ImageName, print_version_of_tools

# this import is required for mypy to work correctly
try:
    from typing import Any, Tuple, Union
except:
    pass

# imports required for annotations to be resolved
from atomic_reactor.util import CommandResult
from atomic_reactor.source import Source

logger = logging.getLogger(__name__)


class ImageAlreadyBuilt(Exception):
    """ This method expects image not to be built but it already is """


class ImageNotBuilt(Exception):
    """ This method expects image to be already built but it is not """


class BuilderStateMachine(object):
    def __init__(self):
        # type: () -> None
        self.is_built = False  # type: bool
        self.image = None  # type: str

    def _ensure_is_built(self):
        # type: () -> None
        """ ensure that image is already built """
        if not self.is_built:
            logger.error("image '%s' is not built yet!", self.image)
            raise ImageNotBuilt()

    def _ensure_not_built(self):
        # type: () -> None
        """ verify that image wasn't built with 'build' method yet """
        if self.is_built:
            logger.error("image '%s' is already built!", self.image)
            raise ImageAlreadyBuilt()


class BuildResult(object):
    def __init__(self, command_result, image_id=None):
        # type: (CommandResult, str) -> None
        """ when build fails, image_id is None """
        self.command_result = command_result
        self._image_id = image_id

    @property
    def image_id(self):
        # type: () -> str
        return self._image_id

    def is_failed(self):
        # type: () -> bool
        return self.command_result.is_failed()

    @property
    def logs(self):
        # type: () -> List[str]
        return self.command_result.logs


class ExceptionBuildResult(object):
    def __init__(self):
        # type: () -> None
        self._logs = traceback.format_exc()

    @property
    def image_id(self):
        # type: () -> None
        return None

    def is_failed(self):
        # type: () -> bool
        return True

    @property
    def logs(self):
        # type: () -> str
        return self._logs


class InsideBuilder(LastLogger, BuilderStateMachine):
    """
    This is expected to run within container
    """

    def __init__(self, source, image, **kwargs):
        # type: (Source, Union[ImageName, str], **Any) -> None
        """
        """
        LastLogger.__init__(self)
        BuilderStateMachine.__init__(self)

        print_version_of_tools()

        self.tasker = DockerTasker()

        info, version = self.tasker.get_info(), self.tasker.get_version()
        logger.debug(json.dumps(info, indent=2))
        logger.info(json.dumps(version, indent=2))

        # arguments for build
        self.source = source  # type: Source
        # TODO: Is it still being used?
        self.base_image_id = None  # type: str
        self.image_id = None  # type: str
        self.built_image_info = None  # type: Dict[str, Any]
        self.image = ImageName.parse(image)  # type: str

        # get info about base image from dockerfile
        self.df_path, self.df_dir = self.source.get_dockerfile_path()  # type: Tuple[str, str]
        self.set_base_image(DockerfileParser(self.df_path).baseimage)
        logger.debug("base image specified in dockerfile = '%s'", self.base_image)
        if not self.base_image.tag:
            self.base_image.tag = 'latest'

    def build(self):
        # TODO: we should rework this to return one type 
        # type: () -> Any
        """
        build image inside current environment;
        it's expected this may run within (privileged) docker container

        :return: image string (e.g. fedora-python:34)
        """
        try:
            logger.info("building image '%s' inside current environment", self.image)
            self._ensure_not_built()
            logger.debug("using dockerfile:\n%s", DockerfileParser(self.df_path).content)
            logs_gen = self.tasker.build_image_from_path(
                self.df_dir,
                self.image,
            )
            logger.debug("build is submitted, waiting for it to finish")
            command_result = wait_for_command(logs_gen)  # wait for build to finish
            logger.info("build was %ssuccessful!", 'un' if command_result.is_failed() else '')
            self.is_built = True
            if not command_result.is_failed():
                self.built_image_info = self.get_built_image_info()
                # self.base_image_id = self.built_image_info['ParentId']  # parent id is not base image!
                self.image_id = self.built_image_info['Id']
            build_result = BuildResult(command_result, self.image_id)
            return build_result
        except:
            logger.exception("build failed")
            return ExceptionBuildResult()

    def set_base_image(self, base_image):
        # type: (str) -> None
        self.base_image = ImageName.parse(base_image)  # type: ImageName

    def inspect_base_image(self):
        # type: () -> Dict[str, Any]
        """
        inspect base image

        :return: dict
        """
        logger.info("inspecting base image '%s'", self.base_image)
        inspect_data = self.tasker.inspect_image(self.base_image)
        return inspect_data

    def inspect_built_image(self):
        # type: () -> Dict[str, Any]
        """
        inspect built image

        :return: dict
        """
        logger.info("inspecting built image '%s'", self.image_id)
        self._ensure_is_built()
        inspect_data = self.tasker.inspect_image(self.image)  # dict with lots of data, see man docker-inspect
        return inspect_data

    def get_base_image_info(self):
        # type: () -> Dict[str, Any]
        """
        query docker about base image

        :return dict
        """
        logger.info("getting information about base image '%s'", self.base_image)
        image_info = self.tasker.get_image_info_by_image_name(self.base_image)
        items_count = len(image_info)
        if items_count == 1:
            return image_info[0]
        elif items_count <= 0:
            logger.error("image '%s' not found", self.base_image)
            raise RuntimeError("image '%s' not found", self.base_image)
        else:
            logger.error("multiple (%d) images found for image '%s'", items_count, self.base_image)
            raise RuntimeError("multiple (%d) images found for image '%s'" % (items_count, self.base_image))

    def get_built_image_info(self):
        # type: () -> Dict[str, Any]
        """
        query docker about built image

        :return dict
        """
        logger.info("getting information about built image '%s'", self.image)
        self._ensure_is_built()
        image = ImageName.parse(self.image)
        image_info = self.tasker.get_image_info_by_image_name(image)
        items_count = len(image_info)
        if items_count == 1:
            return image_info[0]
        elif items_count <= 0:
            logger.error("image '%s' not found", self.image)
            raise RuntimeError("image '%s' not found" % self.image)
        else:
            logger.error("multiple (%d) images found for image '%s'", items_count, self.image)
            raise RuntimeError("multiple (%d) images found for image '%s'" % (items_count, self.image))
