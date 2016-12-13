"""
Copyright (c) 2016 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import print_function, unicode_literals

from atomic_reactor.plugin import BuildStepPlugin
from atomic_reactor.util import get_exported_image_metadata, wait_for_command

import json
import gzip
from subprocess import Popen, PIPE, STDOUT


class FlatpakPlugin(BuildStepPlugin):

    key = 'flatpak'
    is_allowed_to_fail = False

    cwd = None

    def __init__(self, tasker, workflow, export_image=False):
        """
        constructor

        :param tasker: DockerTasker instance
        :param workflow: DockerBuildWorkflow instance
        :param export_image: bool, when True, built image is saved to archive
        """
        super(FlatpakPlugin, self).__init__(tasker, workflow)
        self.export_image = export_image

    def get_flatpak_info(self, df_path):
        result = {}
        with open(df_path) as flakpak_file:
            flakpak_json = json.load(flakpak_file)
            result['branch'] = flakpak_json['branch']
            result['app'] = flakpak_json['app-id']
            version = flakpak_json['runtime-version']
            result['platform'] = "{}//{}".format(flakpak_json['runtime'], version)
            result['sdk'] = "{}//{}".format(flakpak_json['sdk'], version)

        return result

    def run_command(self, cmd):
        process = Popen(cmd, shell=True, cwd=self.cwd, stdout=PIPE, stderr=STDOUT)

        self.log.debug('Running: "%s"' % cmd)
        lines = []
        with process.stdout:
            for line in iter(process.stdout.readline, ''):
                self.log.info(line.strip())
                lines.append(line)
        process.wait()

        command_result = wait_for_command(line for line in lines)

        #if process.returncode != 0:
        #    raise RuntimeError('Error, exit code: %s' % process.returncode)

        return command_result

    def run(self):
        self.cwd = self.workflow.builder.df_dir
        flatpak_info = self.get_flatpak_info(self.workflow.builder.df_path)

        # TODO: read this from env vars?
        self.log.info("Fetching gpg key")
        self.run_command(
            "curl -kLs https://people.gnome.org/~alexl/keys/gnome-sdk.gpg -o gnome-sdk.gpg")

        # TODO: read this from env vars?
        self.log.info("Adding a remote")
        self.run_command(
            "flatpak remote-add --gpg-import=gnome-sdk.gpg origin http://sdk.gnome.org/repo/")

        self.log.info("Installing platform")
        self.run_command(
            "flatpak -v --ostree-verbose install origin {}".format(flatpak_info['platform']))

        self.log.info("Installing SDK")
        self.run_command(
            "flatpak -v --ostree-verbose install origin {}".format(flatpak_info['sdk']))

        self.log.info("Building")
        command_result = self.run_command(
            'flatpak-builder --force-clean --ccache --require-changes --repo=repo --subject="Nightly build of {}, `date`" app flatpak.json'.format(flatpak_info['app']))

        self.log.info("Installing the app")
        self.run_command('flatpak --user remote-add --no-gpg-verify built-repo ./repo')
        self.run_command('flatpak --user install built-repo {}'.format(flatpak_info['app']))
        self.run_command('flatpak --user update {}'.format(flatpak_info['app']))

        self.log.info("Packing in a single bundle")
        outfile = "/{}_flatpak".format(flatpak_info['app'])
        self.run_command(
            'flatpak build-bundle ./repo {0} {1} {2}'.format(
                outfile, flatpak_info['app'], flatpak_info['branch']))

        self.log.info("Compress the flatpak file")
        outfile_zipped = "{}.gz".format(outfile)
        with open(outfile, 'rb') as stream:
            fp = gzip.open(outfile_zipped, 'wb', compresslevel=6)
            _chunk_size = 1024**2  # 1 MB chunk size for reading/writing
            data = stream.read(_chunk_size)
            while data != b'':
                fp.write(data)
                data = stream.read(_chunk_size)

        metadata = get_exported_image_metadata(outfile_zipped)
        self.workflow.exported_image_sequence.append(metadata)

        return command_result
