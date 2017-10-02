"""
Copyright (c) 2017 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.

get the image manifest lists from the worker builders. If possible, group them together
and return them. if not, return the x86_64/amd64 image manifest instead after re-uploading
it for all existing image tags.
"""


from __future__ import unicode_literals
import json
import requests

from atomic_reactor.plugin import PostBuildPlugin, PluginFailedException
from atomic_reactor.util import RegistrySession, registry_hostname, ManifestDigest
from atomic_reactor.constants import PLUGIN_GROUP_MANIFESTS_KEY

MEDIA_TYPE_MANIFEST_V2 = 'application/vnd.docker.distribution.manifest.v2+json'
MEDIA_TYPE_LIST_V2 = 'application/vnd.docker.distribution.manifest.list.v2+json'
MEDIA_TYPE_OCI = 'application/vnd.oci.image.manifest.v1+json'
MEDIA_TYPE_OCI_INDEX = 'application/vnd.oci.image.index.v1+json'


# The plugin requires that the worker builds have already pushed their images into
# each registry that we want the final tags to end up in. There is code here to
# copy images between repositories in a single registry (which is simple, because
# it can be done entirely server-side), but not between registries. Extending the
# code to copy registries is possible, but would be more involved because of the
# size of layers and the complications of the protocol for copying them.


class GroupManifestsPlugin(PostBuildPlugin):
    is_allowed_to_fail = False
    key = PLUGIN_GROUP_MANIFESTS_KEY

    def __init__(self, tasker, workflow, registries, group=True, goarch=None):
        """
        constructor

        :param tasker: DockerTasker instance
        :param workflow: DockerBuildWorkflow instance
        :param registries: dict, keys are docker registries, values are dicts containing
                           per-registry parameters.
                           Params:
                            * "secret" optional string - path to the secret, which stores
                              login and password for remote registry
        :param group: bool, if true, create a manifest list; otherwise only add tags to
                      amd64 image manifest
        :param goarch: dict, keys are platform, values are go language platform names
        """
        # call parent constructor
        super(GroupManifestsPlugin, self).__init__(tasker, workflow)
        self.group = group
        self.goarch = goarch or {}
        self.registries = registries
        self.worker_registries = {}

    def get_manifest(self, session, repository, ref):
        """
        Downloads a manifest from a registry. ref can be a digest, or a tag.
        """
        self.log.debug("%s: Retrieving manifest for %s:%s", session.registry, repository, ref)

        headers = {
            'Accept': ', '.join((
                MEDIA_TYPE_MANIFEST_V2,
                MEDIA_TYPE_LIST_V2,
                MEDIA_TYPE_OCI,
                MEDIA_TYPE_OCI_INDEX
            ))
        }

        url = '/v2/{}/manifests/{}'.format(repository, ref)
        response = session.get(url, headers=headers)
        response.raise_for_status()
        return (response.content,
                response.headers['Docker-Content-Digest'],
                response.headers['Content-Type'],
                int(response.headers['Content-Length']))

    def link_blob_into_repository(self, session, digest, source_repo, target_repo):
        """
        Links ("mounts" in Docker Registry terminology) a blob from one repository in a
        registry into another repository in the same registry.
        """
        self.log.debug("%s: Linking blob %s from %s to %s",
                       session.registry, digest, source_repo, target_repo)

        # Check that it exists in the source repository
        url = "/v2/{}/blobs/{}".format(source_repo, digest)
        result = session.head(url)
        if result.status_code == requests.codes.NOT_FOUND:
            self.log.debug("%s: blob %s, not present in %s, skipping",
                           session.registry, digest, source_repo)
            # Assume we don't need to copy it - maybe it's a foreign layer
            return
        result.raise_for_status()

        url = "/v2/{}/blobs/uploads/?mount={}&from={}".format(target_repo, digest, source_repo)
        result = session.post(url, data='')
        result.raise_for_status()

        if result.status_code != requests.codes.CREATED:
            # A 202-Accepted would mean that the source blob didn't exist and
            # we're starting an upload - but we've checked that above
            raise RuntimeError("Blob mount had unexpected status {}".format(result.status_code))

    def link_manifest_references_into_repository(self, session, manifest, media_type,
                                                 source_repo, target_repo):
        """
        Links all the blobs referenced by the manifest from source_repo into target_repo. If
        manifest_digest is passed, also links the manifest itself. (As a *blob* not as
        a manifest... to use it directly as a manifest, it needs to be uploaded with a tag.)
        """

        if source_repo == target_repo:
            return

        parsed = json.loads(manifest)

        references = []
        if media_type in (MEDIA_TYPE_MANIFEST_V2, MEDIA_TYPE_OCI):
            references.append(parsed['config']['digest'])
            for l in parsed['layers']:
                references.append(l['digest'])
        else:
            # manifest list support could be added here, but isn't needed currently, since
            # we never copy a manifest list as a whole between repositories
            raise RuntimeError("Unhandled media-type {}".format(media_type))

        for digest in references:
            self.link_blob_into_repository(session, digest, source_repo, target_repo)

    def store_manifest_in_repository(self, session, manifest, media_type,
                                     source_repo, target_repo, digest=None, tag=None):
        """
        Stores the manifest into target_repo, possibly tagging it. This may involve
        copying referenced blobs from source_repo.
        """

        if tag:
            self.log.debug("%s: Tagging manifest (or list) from %s as %s:%s",
                           session.registry, source_repo, target_repo, tag)
            ref = tag
        elif digest:
            self.log.debug("%s: Storing manifest (or list) %s from %s in %s",
                           session.registry, digest, source_repo, target_repo)
            ref = digest
        else:
            raise RuntimeError("Either digest or tag must be specified")

        self.link_manifest_references_into_repository(session, manifest, media_type,
                                                      source_repo, target_repo)

        url = '/v2/{}/manifests/{}'.format(target_repo, ref)
        headers = {'Content-Type': media_type}
        response = session.put(url, data=manifest, headers=headers)
        response.raise_for_status()

    def build_list(self, manifests):
        """
        Builds a manifest list or OCI image out of the given manifests
        """

        media_type = manifests[0]['media_type']
        if (not all(m['media_type'] == media_type for m in manifests)):
            raise PluginFailedException('worker manifests have inconsistent types: {}'
                                        .format(manifests))

        if media_type == MEDIA_TYPE_MANIFEST_V2:
            list_type = MEDIA_TYPE_LIST_V2
        elif media_type == MEDIA_TYPE_OCI:
            list_type = MEDIA_TYPE_OCI_INDEX
        else:
            raise PluginFailedException('worker manifests have unsupported type: {}'
                                        .format(media_type))

        return list_type, json.dumps({
                "schemaVersion": 2,
                "mediaType": list_type,
                "manifests": [
                    {
                        "mediaType": media_type,
                        "size": m['size'],
                        "digest": m['digest'],
                        "platform": {
                            "architecture": m['architecture'],
                            "os": "linux"
                        }
                    } for m in manifests
                ],
        }, indent=4)

    def group_manifests_and_tag(self, session, worker_digests):
        """
        Creates a manifest list or OCI image index that groups the different manifests
        in worker_digests, then tags the result with with all the configured tags found
        in workflow.tag_conf.
        """
        self.log.info("%s: Creating manifest list", session.registry)

        # Extract information about the manifests that we will group - we get the
        # size and content type of the manifest by querying the registry
        manifests = []
        for platform, worker_image in worker_digests.items():
            repository = worker_image['repository']
            digest = worker_image['digest']
            content, _, media_type, size = self.get_manifest(session, repository, digest)

            manifests.append({
                'content': content,
                'repository': repository,
                'digest': digest,
                'size': size,
                'media_type': media_type,
                'architecture': self.goarch.get(platform, platform),
            })

        list_type, list_json = self.build_list(manifests)
        self.log.info("%s: Created manifest, Content-Type=%s\n%s", session.registry,
                      list_type, list_json)

        # Now push the manifest list to the registry once per each tag
        self.log.info("%s: Tagging manifest list", session.registry)

        for image in self.workflow.tag_conf.images:
            target_repo = image.to_str(registry=False, tag=False)
            # We have to call store_manifest_in_repository directly for each
            # referenced manifest, since they potentially come from different repos
            for manifest in manifests:
                self.store_manifest_in_repository(session,
                                                  manifest['content'],
                                                  manifest['media_type'],
                                                  manifest['repository'],
                                                  target_repo,
                                                  digest=manifest['digest'])
            self.store_manifest_in_repository(session, list_json, list_type,
                                              target_repo, target_repo, tag=image.tag)
        # Get the digest of the manifest list using one of the tags
        registry_image = self.workflow.tag_conf.unique_images[0]
        _, digest, _, _ = self.get_manifest(session,
                                            registry_image.to_str(registry=False, tag=False),
                                            registry_image.tag)

        if list_type == MEDIA_TYPE_OCI_INDEX:
            digests = ManifestDigest(oci_index=digest)
        else:
            digests = ManifestDigest(v2_list=digest)

        # And store the manifest list in the push_conf
        push_conf_registry = self.workflow.push_conf.add_docker_registry(session.registry,
                                                                         insecure=session.insecure)
        for image in self.workflow.tag_conf.images:
            push_conf_registry.digests[image.tag] = digests

        self.log.info("%s: Manifest list digest is %s", session.registry, digest)

    def tag_manifest_into_registry(self, session, worker_digest):
        """
        Tags the manifest identified by worker_digest into session.registry with all the
        configured tags found in workflow.tag_conf.
        """
        self.log.info("%s: Tagging manifest", session.registry)

        digest = worker_digest['digest']
        source_repo = worker_digest['repository']

        image_manifest, _, media_type, _ = self.get_manifest(session, source_repo, digest)
        if media_type == MEDIA_TYPE_MANIFEST_V2:
            digests = ManifestDigest(v1=digest)
        elif media_type == MEDIA_TYPE_OCI:
            digests = ManifestDigest(oci=digest)
        else:
            raise RuntimeError("Unexpected media type found in worker repository: {}"
                               .format(media_type))

        push_conf_registry = self.workflow.push_conf.add_docker_registry(session.registry,
                                                                         insecure=session.insecure)
        for image in self.workflow.tag_conf.images:
            target_repo = image.to_str(registry=False, tag=False)
            self.store_manifest_in_repository(session, image_manifest, media_type,
                                              source_repo, target_repo, tag=image.tag)

            # add a tag for any plugins running later that expect it
            push_conf_registry.digests[image.tag] = digests

    def sort_annotations(self):
        """
        Return a map of maps to look up a single "worker digest" that has information
        about where to find an image manifest for each registry/architecture combination:

          worker_digest = <result>[registry][architecture]
        """

        all_annotations = self.workflow.build_result.annotations['worker-builds']
        all_platforms = set(all_annotations)
        if len(all_platforms) == 0:
            raise RuntimeError("No worker builds found, cannot group them")

        sorted_digests = {}

        for plat, annotation in all_annotations.items():
            for digest in annotation['digests']:
                hostname = registry_hostname(digest['registry'])

                platforms = sorted_digests.setdefault(hostname, {})
                repos = platforms.setdefault(plat, [])
                repos.append(digest)

        sources = {}
        for registry in self.registries:
            registry_conf = self.registries[registry]
            if registry_conf.get('version') == 'v1':
                continue

            hostname = registry_hostname(registry)
            platforms = sorted_digests.get(hostname, {})

            if set(platforms) != all_platforms:
                raise RuntimeError("Missing platforms for registry {}: found {}, expected {}"
                                   .format(registry, sorted(platforms), sorted(all_platforms)))

            selected_digests = {}
            for p, repos in platforms.items():
                selected_digests[p] = sorted(repos, key=lambda d: d['repository'])[0]

            sources[registry] = selected_digests

        return sources

    def get_registry_session(self, registry):
        registry_conf = self.registries[registry]

        insecure = registry_conf.get('insecure', False)
        secret_path = registry_conf.get('secret')

        return RegistrySession(registry, insecure=insecure, dockercfg_path=secret_path)

    def run(self):
        for registry, source in self.sort_annotations().items():
            session = self.get_registry_session(registry)

            if self.group:
                digest = self.group_manifests_and_tag(session, source)
            else:
                found = False
                for platform, digest in source.items():
                    if self.goarch.get(platform, platform) == 'amd64':
                        self.tag_manifest_into_registry(session, digest)
                        found = True
                if not found:
                    raise ValueError('failed to find an x86_64 platform')
