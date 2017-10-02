"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import unicode_literals

import json
import os

from osbs.api import OSBS
from osbs.conf import Configuration
from osbs.exceptions import OsbsResponseException
from osbs.utils import graceful_chain_get

from atomic_reactor.plugins.pre_add_help import AddHelpPlugin
from atomic_reactor.plugins.post_pulp_pull import PulpPullPlugin
from atomic_reactor.constants import (PLUGIN_KOJI_IMPORT_PLUGIN_KEY,
                                      PLUGIN_KOJI_PROMOTE_PLUGIN_KEY,
                                      PLUGIN_KOJI_UPLOAD_PLUGIN_KEY,
                                      PLUGIN_PULP_PUSH_KEY,
                                      PLUGIN_ADD_FILESYSTEM_KEY,
                                      PLUGIN_BUILD_ORCHESTRATE_KEY,
                                      PLUGIN_GROUP_MANIFESTS_KEY)
from atomic_reactor.plugin import ExitPlugin
from atomic_reactor.util import get_build_json


class StoreMetadataInOSv3Plugin(ExitPlugin):
    key = "store_metadata_in_osv3"
    is_allowed_to_fail = False

    def __init__(self, tasker, workflow, url, verify_ssl=True, use_auth=True):
        """
        constructor

        :param tasker: DockerTasker instance
        :param workflow: DockerBuildWorkflow instance
        :param url: str, URL to OSv3 instance
        :param use_auth: bool, initiate authentication with openshift?
        """
        # call parent constructor
        super(StoreMetadataInOSv3Plugin, self).__init__(tasker, workflow)
        self.url = url
        self.verify_ssl = verify_ssl
        self.use_auth = use_auth

    def get_result(self, result):
        if isinstance(result, Exception):
            result = ''

        return result

    def get_post_result(self, key):
        return self.get_result(self.workflow.postbuild_results.get(key, ''))

    def get_exit_result(self, key):
        return self.get_result(self.workflow.exit_results.get(key, ''))

    def get_config_map(self):
        annotations = self.get_post_result(PLUGIN_KOJI_UPLOAD_PLUGIN_KEY)
        if not annotations:
            return {}

        return annotations

    def get_filesystem_koji_task_id(self):
        res = self.get_result(self.workflow.prebuild_results.get(PLUGIN_ADD_FILESYSTEM_KEY))
        return graceful_chain_get(res, 'filesystem-koji-task-id')

    def get_digests(self):
        """
        Returns a map of repositories to digests
        """

        digests = {}  # repository -> digest
        for registry in self.workflow.push_conf.docker_registries:
            for image in self.workflow.tag_conf.images:
                image_str = image.to_str()
                if image_str in registry.digests:
                    digest = registry.digests[image_str].default
                    digests[image.to_str(registry=False)] = digest

        return digests

    def _get_registries(self):
        """
        Return a list of registries that this build updated

        For orchestrator it should attempt to filter out non-pulp registries, on worker - return
        all registries
        """
        if self.workflow.buildstep_result.get(PLUGIN_BUILD_ORCHESTRATE_KEY):
            registries = self.workflow.push_conf.pulp_registries
            if not registries:
                registries = self.workflow.push_conf.all_registries
            return registries
        else:
            return self.workflow.push_conf.all_registries

    def get_repositories(self):
        # usually repositories formed from NVR labels
        # these should be used for pulling and layering
        primary_repositories = []
        for registry in self._get_registries():
            for image in self.workflow.tag_conf.primary_images:
                registry_image = image.copy()
                registry_image.registry = registry.uri
                primary_repositories.append(registry_image.to_str())

        # unique unpredictable repositories
        unique_repositories = []
        for registry in self._get_registries():
            for image in self.workflow.tag_conf.unique_images:
                registry_image = image.copy()
                registry_image.registry = registry.uri
                unique_repositories.append(registry_image.to_str())

        return {
            "primary": primary_repositories,
            "unique": unique_repositories,
        }

    def get_pullspecs(self, digests):
        # v2 registry digests
        pullspecs = []
        for registry in self._get_registries():
            for image in self.workflow.tag_conf.images:
                if image.to_str() in digests:
                    pullspecs.append({
                        "registry": registry.uri,
                        "repository": image.to_str(registry=False, tag=False),
                        "tag": image.tag or 'latest',
                        "digest": digests[image.to_str()]
                    })
        return pullspecs

    def get_plugin_metadata(self):
        return {
            "errors": self.workflow.plugins_errors,
            "timestamps": self.workflow.plugins_timestamps,
            "durations": self.workflow.plugins_durations,
        }

    def make_labels(self):
        labels = {}

        koji_build_id = self.get_exit_result(PLUGIN_KOJI_IMPORT_PLUGIN_KEY)
        if not koji_build_id:
            koji_build_id = self.get_exit_result(PLUGIN_KOJI_PROMOTE_PLUGIN_KEY)
        if koji_build_id:
            labels["koji-build-id"] = str(koji_build_id)

        filesystem_koji_task_id = self.get_filesystem_koji_task_id()
        if filesystem_koji_task_id:
            labels["filesystem-koji-task-id"] = str(filesystem_koji_task_id)

        updates = self.workflow.build_result.labels
        if updates:
            updates = {key: str(value) for key, value in updates.items()}
            labels.update(updates)

        return labels

    def apply_build_result_annotations(self, annotations):
        updates = self.workflow.build_result.annotations
        if updates:
            updates = {key: json.dumps(value) for key, value in updates.items()}
            annotations.update(updates)

    def run(self):
        metadata = get_build_json().get("metadata", {})

        try:
            build_id = metadata["name"]
        except KeyError:
            self.log.error("malformed build json")
            return
        self.log.info("build id = %s", build_id)

        # initial setup will use host based auth: apache will be set to accept everything
        # from specific IP and will set specific X-Remote-User for such requests
        # FIXME: remove `openshift_uri` once osbs-client is released
        osbs_conf = Configuration(conf_file=None, openshift_uri=self.url, openshift_url=self.url,
                                  use_auth=self.use_auth, verify_ssl=self.verify_ssl,
                                  namespace=metadata.get('namespace', None))
        osbs = OSBS(osbs_conf, osbs_conf)

        try:
            commit_id = self.workflow.source.commit_id
        except AttributeError:
            commit_id = ""

        base_image = self.workflow.builder.base_image
        if base_image is not None:
            base_image_name = base_image.to_str()
            try:
                base_image_id = self.workflow.base_image_inspect['Id']
            except KeyError:
                base_image_id = ""
        else:
            base_image_name = ""
            base_image_id = ""

        try:
            dockerfile_contents = open(self.workflow.builder.df_path).read()
        except AttributeError:
            dockerfile_contents = ""

        annotations = {
            "dockerfile": dockerfile_contents,

            # We no longer store the 'docker build' logs as an annotation
            "logs": '',

            # We no longer store the rpm packages as an annotation
            "rpm-packages": '',

            "repositories": json.dumps(self.get_repositories()),
            "commit_id": commit_id,
            "base-image-id": base_image_id,
            "base-image-name": base_image_name,
            "image-id": self.workflow.builder.image_id or '',
            "digests": json.dumps(self.get_pullspecs(self.get_digests())),
            "plugins-metadata": json.dumps(self.get_plugin_metadata())
        }

        help_result = self.workflow.prebuild_results.get(AddHelpPlugin.key)
        if isinstance(help_result, dict) and 'help_file' in help_result and 'status' in help_result:
            if help_result['status'] == AddHelpPlugin.NO_HELP_FILE_FOUND:
                annotations['help_file'] = json.dumps(None)
            elif help_result['status'] == AddHelpPlugin.HELP_GENERATED:
                annotations['help_file'] = json.dumps(help_result['help_file'])
            else:
                self.log.error("Unknown result from add_help plugin: %s", help_result)

        pulp_push_results = self.workflow.postbuild_results.get(PLUGIN_PULP_PUSH_KEY)
        if pulp_push_results:
            top_layer, _ = pulp_push_results
            annotations['v1-image-id'] = top_layer

        media_types = []
        if pulp_push_results:
            media_types += ['application/json']

        # pulp_pull may run on worker as a postbuild plugin or on orchestrator as an exit plugin
        pulp_pull_results = (self.workflow.postbuild_results.get(PulpPullPlugin.key) or
                             self.workflow.exit_results.get(PulpPullPlugin.key))
        if isinstance(pulp_pull_results, Exception):
            pulp_pull_results = None

        if pulp_pull_results:
            media_types += pulp_pull_results

        if media_types:
            annotations['media-types'] = json.dumps(sorted(list(set(media_types))))

        tar_path = tar_size = tar_md5sum = tar_sha256sum = None
        if len(self.workflow.exported_image_sequence) > 0:
            tar_path = self.workflow.exported_image_sequence[-1].get("path")
            tar_size = self.workflow.exported_image_sequence[-1].get("size")
            tar_md5sum = self.workflow.exported_image_sequence[-1].get("md5sum")
            tar_sha256sum = self.workflow.exported_image_sequence[-1].get("sha256sum")
        # looks like that openshift can't handle value being None (null in json)
        if tar_size is not None and tar_md5sum is not None and tar_sha256sum is not None and \
                tar_path is not None:
            annotations["tar_metadata"] = json.dumps({
                "size": tar_size,
                "md5sum": tar_md5sum,
                "sha256sum": tar_sha256sum,
                "filename": os.path.basename(tar_path),
            })

        annotations.update(self.get_config_map())

        self.apply_build_result_annotations(annotations)

        # For arrangement version 4 onwards (where group_manifests
        # runs in the orchestrator build), restore the repositories
        # metadata which orchestrate_build adjusted.
        if PLUGIN_GROUP_MANIFESTS_KEY in self.workflow.postbuild_results:
            annotations['repositories'] = json.dumps(self.get_repositories())
        try:
            osbs.set_annotations_on_build(build_id, annotations)
        except OsbsResponseException:
            self.log.debug("annotations: %r", annotations)
            raise

        labels = self.make_labels()
        if labels:
            try:
                osbs.update_labels_on_build(build_id, labels)
            except OsbsResponseException:
                self.log.debug("labels: %r", labels)
                raise

        return {"annotations": annotations, "labels": labels}
