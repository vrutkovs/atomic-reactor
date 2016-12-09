"""
Copyright (c) 2016 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import print_function, unicode_literals

from atomic_reactor.plugin import BuildStepPlugin
from atomic_reactor.util import wait_for_command


class FlatpakPlugin(BuildStepPlugin):

    key = 'flatpak'
    is_allowed_to_fail = False

    def __init__(self, tasker, workflow, export_image=False):
        """
        constructor

        :param tasker: DockerTasker instance
        :param workflow: DockerBuildWorkflow instance
        :param export_image: bool, when True, built image is saved to archive
        """
        super(FlatpakPlugin, self).__init__(tasker, workflow)
        self.export_image = export_image

    def run(self):
        builder = self.workflow.builder

        environment = {
            "RUNTIME_REMOTE": "https://sdk.gnome.org/gnome.flatpakrepo",
            "RUNTIME_PLATFORM": "org.gnome.Platform//3.22",
            "RUNTIME_SDK": "org.gnome.Sdk//3.22",
            "SOURCE_GIT": "git://git.gnome.org/gnome-apps-nightly",
            "SOURCE_BRANCH": "gnome-3-22",
            "APPID": "org.gnome.clocks",
            "RSYNC_REPO": "vrutkovs@shell.eng.brq.redhat.com:~/public_html/flatpak/gnome-clocks"
        }

        host_config = self.tasker.d.create_host_config(
            privileged=True,
            network_mode='host')

        result = self.tasker.d.create_container(
            image='brew-pulp-docker01.web.qa.ext.phx1.redhat.com:8888/vrutkovs/flatpak-builder:latest',
            environment = environment,
            command='/bin/bash /build.sh',
            host_config=host_config,
            detach=True)

        container_id = result['Id']

        logs = self.tasker.d.logs(container_id, follow=True, stream=True)
        lines = []
        for line in logs:
            self.log.info(line.strip())
            lines.append(line)
        command_result = wait_for_command(line for line in lines)

        return command_result
