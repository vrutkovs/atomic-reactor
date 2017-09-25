"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""

from atomic_reactor.plugin import PostBuildPlugin
from atomic_reactor.constants import BUILDAH_IMAGE_NAME
from docker.errors import APIError
from subprocess import check_output, check_call


__all__ = ('PostBuildRPMqaPlugin', )


class PostBuildRPMqaPlugin(PostBuildPlugin):
    key = "all_rpm_packages"
    is_allowed_to_fail = False
    rpm_tags = [
        'NAME',
        'VERSION',
        'RELEASE',
        'ARCH',
        'EPOCH',
        'SIZE',
        'SIGMD5',
        'BUILDTIME',
        'SIGPGP:pgpsig',
        'SIGGPG:pgpsig',
    ]
    sep = ';'

    def __init__(self, tasker, workflow, image_id, ignore_autogenerated_gpg_keys=True,
                 use_buildah=False):
        """
        constructor

        :param tasker: DockerTasker instance
        :param workflow: DockerBuildWorkflow instance
        :param use_buildah: Use OCI tools instead of docker
        """
        # call parent constructor
        super(PostBuildRPMqaPlugin, self).__init__(tasker, workflow)
        self.image_id = image_id
        self.ignore_autogenerated_gpg_keys = ignore_autogenerated_gpg_keys
        self.use_buildah = use_buildah

    def run(self):
        fmt = self.sep.join(["%%{%s}" % tag for tag in self.rpm_tags])

        if self.use_buildah:
            # Create a new container
            cmd = ['buildah', 'from', BUILDAH_IMAGE_NAME]
            self.log.debug(' '.join(cmd))
            container_id = check_output(cmd).strip()

            # Run command in container
            cmd = "buildah run {0} -- /bin/rpm -qa --qf '{1}\n'".format(container_id, fmt)
            self.log.debug(cmd)
            plugin_output = check_output(cmd, shell=True)
        else:
            container_id = self.tasker.run(
                self.image_id,
                command="-qa --qf '{0}\n'".format(fmt),
                create_kwargs={"entrypoint": "/bin/rpm"},
                start_kwargs={},
            )
            self.tasker.wait(container_id)
            plugin_output = self.tasker.logs(container_id, stream=False)

        # gpg-pubkey are autogenerated packages by rpm when you import a gpg key
        # these are of course not signed, let's ignore those by default
        if self.ignore_autogenerated_gpg_keys:
            self.log.debug("ignore rpms 'gpg-pubkey'")
            plugin_output = [x for x in plugin_output if not x.startswith("gpg-pubkey" + self.sep)]

        if self.use_buildah:
            cmd = ['buildah', 'rm', container_id]
            self.log.debug(' '.join(cmd))
            check_output(cmd)
        else:
            volumes = self.tasker.get_volumes_for_container(container_id)
            try:
                self.tasker.remove_container(container_id)
            except APIError:
                self.log.warning("error removing container (ignored):",
                                 exc_info=True)

            for volume_name in volumes:
                try:
                    self.tasker.remove_volume(volume_name)
                except APIError:
                    self.log.warning("error removing volume (ignored):", exc_info=True)

        return plugin_output
