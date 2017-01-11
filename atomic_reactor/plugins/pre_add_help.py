"""
Copyright (c) 2017 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.


Convert a help markdown file a man page and store it to /help.1 in the image
so that 'atomic help' could display it.
This is accomplished by appending an ADD command to it.

Example configuration:
{
    'name': 'add_help',
    'args': {'help_file': 'help.md'}
}
"""

import os
from subprocess import check_output, CalledProcessError, STDOUT
from atomic_reactor.plugin import PreBuildPlugin
from atomic_reactor.util import df_parser


class AddHelpPlugin(PreBuildPlugin):
    key = "add_help"
    man_filename = "help.1"

    def __init__(self, tasker, workflow, help_file="help.md"):
        """
        constructor

        :param tasker: DockerTasker instance
        :param workflow: DockerBuildWorkflow instance
        :param help_file: filename of the markdown help file
        """
        # call parent constructor
        super(AddHelpPlugin, self).__init__(tasker, workflow)
        self.help_file = help_file

    def run(self):
        """
        run the plugin
        """

        help_path = os.path.join(self.workflow.builder.df_dir, self.help_file)

        if not os.path.exists(help_path):
            self.log.info("File %s not found", help_path)
            return

        man_path = os.path.join(self.workflow.builder.df_dir, self.man_filename)

        go_md2man_cmd = [u'go-md2man', u'-in={}'.format(help_path), u'-out={}'.format(man_path)]

        try:
            check_output(go_md2man_cmd, stderr=STDOUT)
        except CalledProcessError as e:
            if e.returncode == 127:
                raise RuntimeError(
                    "Help file is available, but go-md2man is not present in a buildroot")
            raise RuntimeError("Error running %s: %r, exit code: %s, output: '%s'" % (
                e.cmd, e, e.returncode, e.output))

        if not os.path.exists(man_path):
            raise RuntimeError("go-md2man run complete, but man file is not found")

        # Include the help file in the docker file
        dockerfile = df_parser(self.workflow.builder.df_path, workflow=self.workflow)
        lines = dockerfile.lines

        content = 'ADD {0} /{0}'.format(self.man_filename)
        # put it before last instruction
        lines.insert(-1, content + '\n')

        dockerfile.lines = lines

        self.log.info("added %s", man_path)

        return content
