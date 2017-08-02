"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import print_function, unicode_literals

import os

try:
    import koji as koji
except ImportError:
    import inspect
    import sys

    # Find our mocked koji module
    import tests.koji as koji
    mock_koji_path = os.path.dirname(inspect.getfile(koji.ClientSession))
    if mock_koji_path not in sys.path:
        sys.path.append(os.path.dirname(mock_koji_path))

    # Now load it properly, the same way the plugin will
    del koji
    import koji as koji
    sys.path.remove(os.path.dirname(mock_koji_path))

from atomic_reactor.plugins.pre_koji import KojiPlugin
from atomic_reactor.core import DockerTasker
from atomic_reactor.inner import DockerBuildWorkflow
from atomic_reactor.plugin import PreBuildPluginsRunner
from atomic_reactor.util import ImageName
from flexmock import flexmock
import pytest
from tests.constants import SOURCE, MOCK
if MOCK:
    from tests.docker_mock import mock_docker


class X(object):
    pass


KOJI_TARGET = "target"
KOJI_TARGET_BROKEN_TAG = "target-broken"
KOJI_TARGET_BROKEN_REPO = "target-broken-repo"
KOJI_TAG = "tag"
KOJI_BROKEN_TAG = "tag-broken"
KOJI_BROKEN_REPO = "tag-broken-repo"
GET_TARGET_RESPONSE = {"build_tag_name": KOJI_TAG}
BROKEN_TAG_RESPONSE = {"build_tag_name": KOJI_BROKEN_TAG}
BROKEN_REPO_RESPONSE = {"build_tag_name": KOJI_BROKEN_REPO}
TAG_ID = "1"
BROKEN_REPO_TAG_ID = "2"
GET_TAG_RESPONSE = {"id": TAG_ID, "name": KOJI_TAG}
REPO_ID = "2"
BROKEN_REPO_ID = "3"
REPO_BROKEN_TAG_RESPONSE = {"id": BROKEN_REPO_ID, "name": KOJI_BROKEN_REPO}
GET_REPO_RESPONSE = {"id": "2"}
ROOT = "http://example.com"


# ClientSession is xmlrpc instance, we need to mock it explicitly
class MockedClientSession(object):
    def __init__(self, hub, opts=None):
        pass

    def getBuildTarget(self, target):
        if target == KOJI_TARGET_BROKEN_TAG:
            return BROKEN_TAG_RESPONSE
        if target == KOJI_TARGET_BROKEN_REPO:
            return BROKEN_REPO_RESPONSE
        return GET_TARGET_RESPONSE

    def getTag(self, tag):
        if tag == KOJI_BROKEN_TAG:
            return None
        if tag == KOJI_BROKEN_REPO:
            return REPO_BROKEN_TAG_RESPONSE
        return GET_TAG_RESPONSE

    def getRepo(self, repo):
        if repo == BROKEN_REPO_ID:
            return None
        return GET_REPO_RESPONSE

    def ssl_login(self, cert, ca, serverca, proxyuser=None):
        self.ca_path = ca
        self.cert_path = cert
        self.serverca_path = serverca
        return True


class MockedPathInfo(object):
    def __init__(self, topdir=None):
        self.topdir = topdir

    def repo(self, repo_id, name):
        return "{0}/repos/{1}/{2}".format(self.topdir, name, repo_id)


def prepare():
    if MOCK:
        mock_docker()
    tasker = DockerTasker()
    workflow = DockerBuildWorkflow(SOURCE, "test-image")
    setattr(workflow, 'builder', X())

    setattr(workflow.builder, 'image_id', "asd123")
    setattr(workflow.builder, 'base_image', ImageName(repo='Fedora', tag='21'))
    setattr(workflow.builder, 'source', X())
    setattr(workflow.builder.source, 'dockerfile_path', None)
    setattr(workflow.builder.source, 'path', None)

    session = MockedClientSession(hub='', opts=None)
    workflow.koji_session = session
    flexmock(koji,
             ClientSession=session,
             PathInfo=MockedPathInfo)

    return tasker, workflow


class TestKoji(object):
    @pytest.mark.parametrize(('target', 'expect_success'), [
        (KOJI_TARGET, True),
        (KOJI_TARGET_BROKEN_TAG, False),
        (KOJI_TARGET_BROKEN_REPO, False)])
    @pytest.mark.parametrize(('root',
                              'koji_ssl_certs',
                              'expected_string',
                              'expected_file',
                              'proxy'), [
        # Plain http repo
        ('http://example.com',
         False,
         None,
         None,
         None),

        # Plain http repo with proxy
        ('http://example.com',
         False,
         None,
         None,
         'http://proxy.example.com'),

        # https with koji_ssl_certs
        # ('https://example.com',
        #  True,
        #  'sslcacert=',
        #  '/etc/yum.repos.d/example.com.cert'),

        # https with no cert available
        ('https://nosuchwebsiteforsure.com',
         False,
         'sslverify=0',
         None,
         None),

        # https with no cert available
        ('https://nosuchwebsiteforsure.com',
         False,
         'sslverify=0',
         None,
         'http://proxy.example.com'),

        # https with cert available
        # ('https://example.com',
        #  False,
        #  'sslcacert=/etc/yum.repos.d/example.com.cert',
        #  '/etc/yum.repos.d/example.com.cert'),

        # https with a cert for authentication
        ('https://nosuchwebsiteforsure.com',
         True,
         'sslverify=0',
         None,
         'http://proxy.example.com'),


    ])
    def test_koji_plugin(self,
                         target, expect_success,
                         tmpdir, root, koji_ssl_certs,
                         expected_string, expected_file, proxy):
        tasker, workflow = prepare()
        args = {
            'target': target,
            'hub': '',
            'root': root,
            'proxy': proxy,
        }

        if koji_ssl_certs:
            args['koji_ssl_certs_dir'] = str(tmpdir)
            with open('{}/ca'.format(tmpdir), 'w') as ca_fd:
                ca_fd.write('ca')
            with open('{}/cert'.format(tmpdir), 'w') as cert_fd:
                cert_fd.write('cert')
            with open('{}/serverca'.format(tmpdir), 'w') as serverca_fd:
                serverca_fd.write('serverca')

        runner = PreBuildPluginsRunner(tasker, workflow, [{
            'name': KojiPlugin.key,
            'args': args,
        }])

        runner.run()

        if not expect_success:
            return

        if koji_ssl_certs:
            for file_path, expected in [(workflow.koji_session.ca_path, 'ca'),
                                        (workflow.koji_session.cert_path, 'cert'),
                                        (workflow.koji_session.serverca_path, 'serverca')]:

                assert os.path.isfile(file_path)
                with open(file_path, 'r') as fd:
                    assert fd.read() == expected

        repofile = '/etc/yum.repos.d/target.repo'
        assert repofile in workflow.files
        content = workflow.files[repofile]
        assert content.startswith("[atomic-reactor-koji-plugin-target]\n")
        assert "gpgcheck=0\n" in content
        assert "enabled=1\n" in content
        assert "name=atomic-reactor-koji-plugin-target\n" in content
        assert "baseurl=%s/repos/tag/2/$basearch\n" % root in content

        if proxy:
            assert "proxy=%s" % proxy in content

        if expected_string:
            assert expected_string in content

        if expected_file:
            assert expected_file in workflow.files
