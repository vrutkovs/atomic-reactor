"""
Copyright (c) 2017 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""

import yaml
import subprocess

from atomic_reactor.plugin import PostBuildPlugin
from tempfile import NamedTemporaryFile


class GroupManifestsPlugin(PostBuildPlugin):
    """
    Create a manifest list out of worker images
    """

    key = "group_manifests"
    is_allowed_to_fail = False

    def __init__(self, tasker, workflow, registries):
        """
        constructor

        :param tasker: DockerTasker instance
        :param workflow: DockerBuildWorkflow instance
        """
        # call parent constructor
        super(GroupManifestsPlugin, self).__init__(tasker, workflow)
        self.registries = registries

    def submit_manifest_list(self, registry, registry_conf, manifest_list_dict):
        docker_push_secret = registry_conf.get('secret', None)
        with NamedTemporaryFile(prefix='manifest-list', suffix=".yml", mode='w') as fp:
            yaml.dump(manifest_list_dict, stream=fp)
            fp.flush()
            self.log.info("Wrote to file %s", fp.name)

            cmd = ['manifest-tool', '--docker-cfg=%s' % docker_push_secret,
                   'push', 'from-spec', fp.name]
            subprocess.check_call(cmd)
            self.log.info("Manifest list submitted")

    def run(self):
        pushed_images = []

        if not self.workflow.tag_conf.unique_images:
            self.workflow.tag_conf.add_unique_image(self.workflow.image)

        for registry, registry_conf in self.registries.items():
            for image in self.workflow.tag_conf.images:
                manifest_list_image = image.copy()
                manifest_list_image.registry = registry
                manifest_list_image.tag = "ml-%s" % manifest_list_image.tag

                manifest_list_dict = {}
                manifest_list_dict['image'] = manifest_list_image.to_str()
                manifest_list_dict['manifests'] = []

                for arch_image, arch_name in [(image, 'amd64')]:
                    arch_image.registry = registry
                    arch_manifest = {
                        'platform': {
                            'architecture': arch_name,
                            'os': 'linux'
                        },
                        'image': image.to_str(registry=True)
                    }
                    manifest_list_dict['manifests'].append(arch_manifest)
                self.log.info("Submitting manifest-list %s", manifest_list_dict)
                self.submit_manifest_list(registry, registry_conf, manifest_list_dict)
                pushed_images.append(manifest_list_image)

        self.log.info("All images were tagged and pushed")
        return pushed_images
