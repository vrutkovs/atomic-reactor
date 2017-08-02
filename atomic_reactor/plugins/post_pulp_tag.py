"""
Copyright (c) 2017 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""

from __future__ import print_function, unicode_literals

import tempfile
import json

from atomic_reactor.plugin import PostBuildPlugin
from atomic_reactor.plugins.build_orchestrate_build import get_worker_build_info
from atomic_reactor.constants import PLUGIN_PULP_TAG_KEY
from atomic_reactor.pulp_util import PulpHandler


class PulpTagPlugin(PostBuildPlugin):
    """
    Find a platform with a v1-image-id annotation and tag it in pulp with the value of that
    annotation. Raise an error if two tags have that annotation.

    """

    key = PLUGIN_PULP_TAG_KEY
    is_allowed_to_fail = False

    def __init__(self, tasker, workflow, pulp_registry_name, pulp_secret_path=None,
                 username=None, password=None, dockpulp_loglevel=None):
        """
        constructor

        :param tasker: DockerTasker instance
        :param workflow: DockerBuildWorkflow instance
        :param pulp_registry_name: str, name of pulp registry to use, specified in /etc/
                                   dockpulp.conf
        :param pulp_secret_path: path to pulp.cer and pulp.key; $SOURCE_SECRET_PATH otherwise
        :param username: pulp username, used in preference to certificate and key
        :param password: pulp password, used in preference to certificate and key
        """
        # call parent constructor
        super(PulpTagPlugin, self).__init__(tasker, workflow)
        self.pulp_registry_name = pulp_registry_name
        self.pulp_secret_path = pulp_secret_path
        self.username = username
        self.password = password

        self.dockpulp_loglevel = dockpulp_loglevel

    def set_v1_tag(self, v1_image_id):
        image_names = self.workflow.tag_conf.images[:]
        # Work out image ID
        image = self.workflow.image
        self.log.info("fetching image %s from docker", image)
        with tempfile.NamedTemporaryFile(prefix='docker-image-', suffix='.tar') as image_file:
            image_file.write(self.tasker.d.get_image(image).data)
            # This file will be referenced by its filename, not file
            # descriptor - must ensure contents are written to disk
            image_file.flush()
        handler = PulpHandler(self.workflow, self.pulp_registry_name, self.log,
                              pulp_secret_path=self.pulp_secret_path, username=self.username,
                              password=self.password, dockpulp_loglevel=self.dockpulp_loglevel)

        pulp_repos = handler.create_dockpulp_and_repos(image_file.name, image_names)
        for repo_id, pulp_repo in pulp_repos.items():
            handler.update_repo(repo_id, {"tag": "%s:%s" % (",".join(pulp_repo.tags), v1_image_id)})

    def run(self):
        """
        Run the plugin.
        """

        worker_builds = self.workflow.build_result.annotations['worker-builds']
        has_v1_image_id = None

        for platform in worker_builds:
            build_info = get_worker_build_info(self.workflow, platform)
            annotations = build_info.build.get_annotations()
            v1_image_id = annotations.get('v1-image-id')
            if v1_image_id:
                v1_image_id = json.loads(v1_image_id)
                if has_v1_image_id:
                    msg = "two platforms with v1-image-ids: {0} and {0}".format(platform,
                                                                                has_v1_image_id)
                    raise RuntimeError(msg)
                has_v1_image_id = platform
                self.log.info("tagging v1-image-id %s to platform %s", v1_image_id, platform)
                self.set_v1_tag(v1_image_id)
