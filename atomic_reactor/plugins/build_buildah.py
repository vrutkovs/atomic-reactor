"""
Copyright (c) 2016 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import print_function, unicode_literals

from atomic_reactor.build import BuildResult
from atomic_reactor.plugin import BuildStepPlugin
from atomic_reactor.util import get_exported_image_metadata
from atomic_reactor.constants import IMAGE_TYPE_DOCKER_ARCHIVE

from subprocess import Popen, PIPE, STDOUT, check_call
import os.path


EXPORTED_BUILT_IMAGE_NAME = 'built-image.tar'


class BuildahPlugin(BuildStepPlugin):

    key = 'buildah'
    buildah_params = []

    def __init__(self, tasker, workflow, export_image=True):
        """
        constructor

        :param tasker: DockerTasker instance
        :param workflow: DockerBuildWorkflow instance
        :param export_image: bool, when True, built image is saved to archive
        """
        super(BuildahPlugin, self).__init__(tasker, workflow)
        self.export_image = export_image

    def run(self):
        builder = self.workflow.builder

        self.log.debug('Building image')
        image = builder.image.to_str()
        cmd = [
            'buildah',
            'bud',
            '--pull=false',
            '--tag', image,
            '--format', 'docker',
            builder.df_dir,
        ]
        self.log.debug(' '.join(cmd))
        bud_process = Popen(cmd, stdout=PIPE, stderr=STDOUT)
        lines = []
        with bud_process.stdout:
            for line in iter(bud_process.stdout.readline, ''):
                self.log.info(line.strip())
                lines.append(line)
        bud_process.wait()

        if bud_process.returncode != 0:
            return BuildResult(logs=lines, fail_reason="image not built")

        self.log.debug('Fetching image ID')
        image_id = builder.tasker.get_image_id_via_skopeo("containers-storage:{}".format(image))
        self.log.debug("image ID: {}".format(image_id))
        result = BuildResult(logs=lines, image_id=image_id)

        if self.export_image:
            self.log.info('Saving image into archive')
            outfile = os.path.join(self.workflow.source.workdir,
                                   EXPORTED_BUILT_IMAGE_NAME)
            cmd = [
                "skopeo",
                "copy",
                "containers-storage:docker.io/{}".format(image),
                "docker-archive:{0}:{1}".format(outfile, image),
            ]
            self.log.debug(' '.join(cmd))
            check_call(['sleep', 'infinity'])
            skopeo_process = Popen(cmd, stdout=PIPE, stderr=STDOUT)
            lines = []
            with skopeo_process.stdout:
                for line in iter(skopeo_process.stdout.readline, ''):
                    self.log.info(line.strip())
                    lines.append(line)
            skopeo_process.wait()
            if skopeo_process.returncode != 0:
                raise RuntimeError("archive is not created")

            metadata = get_exported_image_metadata(outfile, IMAGE_TYPE_DOCKER_ARCHIVE)
            self.workflow.exported_image_sequence.append(metadata)

        return result
