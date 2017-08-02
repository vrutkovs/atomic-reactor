"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""

from __future__ import unicode_literals

import json
import os

try:
    import koji
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
    import koji
    sys.path.remove(os.path.dirname(mock_koji_path))

from atomic_reactor.core import DockerTasker
from atomic_reactor.plugins.exit_koji_promote import (KojiUploadLogger,
                                                      KojiPromotePlugin)
from atomic_reactor.plugins.exit_koji_tag_build import KojiTagBuildPlugin
from atomic_reactor.plugins.post_rpmqa import PostBuildRPMqaPlugin
from atomic_reactor.plugins.pre_check_and_set_rebuild import CheckAndSetRebuildPlugin
from atomic_reactor.plugins.pre_add_filesystem import AddFilesystemPlugin
from atomic_reactor.plugins.pre_add_help import AddHelpPlugin
from atomic_reactor.plugin import ExitPluginsRunner, PluginFailedException
from atomic_reactor.inner import DockerBuildWorkflow, TagConf, PushConf
from atomic_reactor.util import ImageName, ManifestDigest
from atomic_reactor.source import GitSource, PathSource
from atomic_reactor.build import BuildResult
from tests.constants import SOURCE, MOCK

from flexmock import flexmock
import pytest
from tests.docker_mock import mock_docker
import subprocess
from osbs.api import OSBS
from osbs.exceptions import OsbsException
from six import string_types

NAMESPACE = 'mynamespace'
BUILD_ID = 'build-1'


class X(object):
    pass


class MockedPodResponse(object):
    def get_container_image_ids(self):
        return {'buildroot:latest': '0123456'}


class MockedClientSession(object):
    TAG_TASK_ID = 1234
    DEST_TAG = 'images-candidate'

    def __init__(self, hub, opts=None, task_states=None):
        self.uploaded_files = []
        self.build_tags = {}
        self.task_states = task_states or ['FREE', 'ASSIGNED', 'CLOSED']

        self.task_states = list(self.task_states)
        self.task_states.reverse()
        self.tag_task_state = self.task_states.pop()

    def krb_login(self, principal=None, keytab=None, proxyuser=None):
        return True

    def ssl_login(self, cert, ca, serverca, proxyuser=None):
        return True

    def logout(self):
        pass

    def uploadWrapper(self, localfile, path, name=None, callback=None,
                      blocksize=1048576, overwrite=True):
        self.uploaded_files.append(path)
        self.blocksize = blocksize

    def CGImport(self, metadata, server_dir):
        self.metadata = metadata
        self.server_dir = server_dir
        return {"id": "123"}

    def getBuildTarget(self, target):
        return {'dest_tag_name': self.DEST_TAG}

    def tagBuild(self, tag, build, force=False, fromtag=None):
        self.build_tags[build] = tag
        return self.TAG_TASK_ID

    def getTaskInfo(self, task_id, request=False):
        assert task_id == self.TAG_TASK_ID

        # For extra code coverage, imagine Koji denies the task ever
        # existed.
        if self.tag_task_state is None:
            return None

        return {'state': koji.TASK_STATES[self.tag_task_state]}

    def taskFinished(self, task_id):
        try:
            self.tag_task_state = self.task_states.pop()
        except IndexError:
            # No more state changes
            pass

        return self.tag_task_state in ['CLOSED', 'FAILED', 'CANCELED', None]


FAKE_SIGMD5 = b'0' * 32
FAKE_RPM_OUTPUT = (
    b'name1;1.0;1;x86_64;0;' + FAKE_SIGMD5 + b';(none);'
    b'RSA/SHA256, Mon 29 Jun 2015 13:58:22 BST, Key ID abcdef01234567\n'

    b'gpg-pubkey;01234567;01234567;(none);(none);(none);(none);(none)\n'

    b'gpg-pubkey-doc;01234567;01234567;noarch;(none);' + FAKE_SIGMD5 +
    b';(none);(none)\n'

    b'name2;2.0;2;x86_64;0;' + FAKE_SIGMD5 + b';' +
    b'RSA/SHA256, Mon 29 Jun 2015 13:58:22 BST, Key ID bcdef012345678;(none)\n'
    b'\n')

FAKE_OS_OUTPUT = 'fedora-22'


def fake_subprocess_output(cmd):
    if cmd.startswith('/bin/rpm'):
        return FAKE_RPM_OUTPUT
    elif 'os-release' in cmd:
        return FAKE_OS_OUTPUT
    else:
        raise RuntimeError


class MockedPopen(object):
    def __init__(self, cmd, *args, **kwargs):
        self.cmd = cmd

    def wait(self):
        return 0

    def communicate(self):
        return (fake_subprocess_output(self.cmd), '')


def fake_Popen(cmd, *args, **kwargs):
    return MockedPopen(cmd, *args, **kwargs)


def fake_digest(image):
    tag = image.to_str(registry=False)
    return 'sha256:{0:032x}'.format(len(tag))


def is_string_type(obj):
    return any(isinstance(obj, strtype)
               for strtype in string_types)


def mock_environment(tmpdir, session=None, name=None,
                     component=None, version=None, release=None,
                     source=None, build_process_failed=False,
                     is_rebuild=True, docker_registry=True,
                     pulp_registries=0, blocksize=None,
                     task_states=None, additional_tags=None,
                     has_config=None,
                     logs_return_bytes=True):
    if session is None:
        session = MockedClientSession('', task_states=None)
    if source is None:
        source = GitSource('git', 'git://hostname/path')

    if MOCK:
        mock_docker()
    tasker = DockerTasker()
    workflow = DockerBuildWorkflow(SOURCE, "test-image")
    base_image_id = '123456parent-id'
    setattr(workflow, '_base_image_inspect', {'Id': base_image_id})
    setattr(workflow, 'builder', X())
    setattr(workflow.builder, 'image_id', '123456imageid')
    setattr(workflow.builder, 'base_image', ImageName(repo='Fedora', tag='22'))
    setattr(workflow.builder, 'source', X())
    setattr(workflow.builder, 'built_image_info', {'ParentId': base_image_id})
    setattr(workflow.builder.source, 'dockerfile_path', None)
    setattr(workflow.builder.source, 'path', None)
    setattr(workflow, 'tag_conf', TagConf())
    with open(os.path.join(str(tmpdir), 'Dockerfile'), 'wt') as df:
        df.write('FROM base\n'
                 'LABEL BZComponent={component} com.redhat.component={component}\n'
                 'LABEL Version={version} version={version}\n'
                 'LABEL Release={release} release={release}\n'
                 .format(component=component, version=version, release=release))
        setattr(workflow.builder, 'df_path', df.name)
    if name and version:
        workflow.tag_conf.add_unique_image('user/test-image:{v}-timestamp'
                                           .format(v=version))
    if name and version and release:
        workflow.tag_conf.add_primary_images(["{0}:{1}-{2}".format(name,
                                                                   version,
                                                                   release),
                                              "{0}:{1}".format(name, version),
                                              "{0}:latest".format(name)])

    if additional_tags:
        workflow.tag_conf.add_primary_images(["{0}:{1}".format(name, tag)
                                              for tag in additional_tags])

    flexmock(subprocess, Popen=fake_Popen)
    flexmock(koji, ClientSession=lambda hub, opts: session)
    flexmock(GitSource)
    if logs_return_bytes:
        logs = b'build logs - \xe2\x80\x98 \xe2\x80\x97 \xe2\x80\x99'
    else:
        logs = 'build logs - \u2018 \u2017 \u2019'
    (flexmock(OSBS)
        .should_receive('get_build_logs')
        .with_args(BUILD_ID)
        .and_return(logs))
    (flexmock(OSBS)
        .should_receive('get_pod_for_build')
        .with_args(BUILD_ID)
        .and_return(MockedPodResponse()))
    setattr(workflow, 'source', source)
    setattr(workflow.source, 'lg', X())
    setattr(workflow.source.lg, 'commit_id', '123456')
    setattr(workflow, 'push_conf', PushConf())
    if docker_registry:
        docker_reg = workflow.push_conf.add_docker_registry('docker.example.com')

        for image in workflow.tag_conf.images:
            tag = image.to_str(registry=False)
            if pulp_registries:
                docker_reg.digests[tag] = ManifestDigest(v1=fake_digest(image),
                                                         v2='sha256:not-used')
            else:
                docker_reg.digests[tag] = ManifestDigest(v1='sha256:not-used',
                                                         v2=fake_digest(image))

            if has_config:
                docker_reg.config = {
                    'config': {'architecture': 'x86_64'},
                    'container_config': {}
                }

    for pulp_registry in range(pulp_registries):
        workflow.push_conf.add_pulp_registry('env', 'pulp.example.com')

    with open(os.path.join(str(tmpdir), 'image.tar.xz'), 'wt') as fp:
        fp.write('x' * 2**12)
        setattr(workflow, 'exported_image_sequence', [{'path': fp.name}])

    if build_process_failed:
        workflow.build_result = BuildResult(logs=["docker build log - \u2018 \u2017 \u2019 \n'"],
                                            fail_reason="not built")
    else:
        workflow.build_result = BuildResult(logs=["docker build log - \u2018 \u2017 \u2019 \n'"],
                                            image_id="id1234")
    workflow.prebuild_plugins_conf = {}
    workflow.prebuild_results[CheckAndSetRebuildPlugin.key] = is_rebuild
    workflow.postbuild_results[PostBuildRPMqaPlugin.key] = [
        "name1;1.0;1;x86_64;0;2000;" + FAKE_SIGMD5.decode() + ";23000;"
        "RSA/SHA256, Tue 30 Aug 2016 00:00:00, Key ID 01234567890abc;(none)",
        "name2;2.0;1;x86_64;0;3000;" + FAKE_SIGMD5.decode() + ";24000"
        "RSA/SHA256, Tue 30 Aug 2016 00:00:00, Key ID 01234567890abd;(none)",
    ]

    return tasker, workflow


@pytest.fixture
def os_env(monkeypatch):
    monkeypatch.setenv('BUILD', json.dumps({
        "metadata": {
            "creationTimestamp": "2015-07-27T09:24:00Z",
            "namespace": NAMESPACE,
            "name": BUILD_ID,
        }
    }))
    monkeypatch.setenv('OPENSHIFT_CUSTOM_BUILD_BASE_IMAGE', 'buildroot:latest')


def create_runner(tasker, workflow, ssl_certs=False, principal=None,
                  keytab=None, metadata_only=False, blocksize=None,
                  target=None, tag_later=False):
    args = {
        'kojihub': '',
        'url': '/',
    }
    if ssl_certs:
        args['koji_ssl_certs'] = '/'

    if principal:
        args['koji_principal'] = principal

    if keytab:
        args['koji_keytab'] = keytab

    if metadata_only:
        args['metadata_only'] = True

    if blocksize:
        args['blocksize'] = blocksize

    if target:
        args['target'] = target
        args['poll_interval'] = 0

    plugins_conf = [
        {'name': KojiPromotePlugin.key, 'args': args},
    ]

    if target and tag_later:
        plugins_conf.append({'name': KojiTagBuildPlugin.key,
                             'args': {
                                 'kojihub': '',
                                 'target': target,
                                 'poll_interval': 0.01}})
    workflow.exit_plugins_conf = plugins_conf
    runner = ExitPluginsRunner(tasker, workflow, plugins_conf)
    return runner


class TestKojiUploadLogger(object):
    @pytest.mark.parametrize('totalsize', [0, 1024])
    def test_with_zero(self, totalsize):
        logger = flexmock()
        logger.should_receive('debug').once()
        upload_logger = KojiUploadLogger(logger)
        upload_logger.callback(0, totalsize, 0, 0, 0)

    @pytest.mark.parametrize(('totalsize', 'step', 'expected_times'), [
        (10, 1, 11),
        (12, 1, 7),
        (12, 3, 5),
    ])
    def test_with_defaults(self, totalsize, step, expected_times):
        logger = flexmock()
        logger.should_receive('debug').times(expected_times)
        upload_logger = KojiUploadLogger(logger)
        upload_logger.callback(0, totalsize, 0, 0, 0)
        for offset in range(step, totalsize + step, step):
            upload_logger.callback(offset, totalsize, step, 1.0, 1.0)

    @pytest.mark.parametrize(('totalsize', 'step', 'notable', 'expected_times'), [
        (10, 1, 10, 11),
        (10, 1, 20, 6),
        (10, 1, 25, 5),
        (12, 3, 25, 5),
    ])
    def test_with_notable(self, totalsize, step, notable, expected_times):
        logger = flexmock()
        logger.should_receive('debug').times(expected_times)
        upload_logger = KojiUploadLogger(logger, notable_percent=notable)
        for offset in range(0, totalsize + step, step):
            upload_logger.callback(offset, totalsize, step, 1.0, 1.0)


class TestKojiPromote(object):
    def test_koji_promote_failed_build(self, tmpdir, os_env):
        session = MockedClientSession('')
        tasker, workflow = mock_environment(tmpdir,
                                            session=session,
                                            build_process_failed=True,
                                            name='ns/name',
                                            version='1.0',
                                            release='1')
        runner = create_runner(tasker, workflow)
        runner.run()

        # Must not have promoted this build
        assert not hasattr(session, 'metadata')

    def test_koji_promote_no_tagconf(self, tmpdir, os_env):
        tasker, workflow = mock_environment(tmpdir)
        runner = create_runner(tasker, workflow)
        with pytest.raises(PluginFailedException):
            runner.run()

    def test_koji_promote_no_build_env(self, tmpdir, monkeypatch, os_env):
        tasker, workflow = mock_environment(tmpdir,
                                            name='ns/name',
                                            version='1.0',
                                            release='1')
        runner = create_runner(tasker, workflow)

        # No BUILD environment variable
        monkeypatch.delenv("BUILD", raising=False)

        with pytest.raises(PluginFailedException) as exc:
            runner.run()
        assert "plugin 'koji_promote' raised an exception: KeyError" in str(exc)

    def test_koji_promote_no_build_metadata(self, tmpdir, monkeypatch, os_env):
        tasker, workflow = mock_environment(tmpdir,
                                            name='ns/name',
                                            version='1.0',
                                            release='1')
        runner = create_runner(tasker, workflow)

        # No BUILD metadata
        monkeypatch.setenv("BUILD", json.dumps({}))
        with pytest.raises(PluginFailedException):
            runner.run()

    def test_koji_promote_wrong_source_type(self, tmpdir, os_env):
        source = PathSource('path', 'file:///dev/null')
        tasker, workflow = mock_environment(tmpdir,
                                            name='ns/name',
                                            version='1.0',
                                            release='1',
                                            source=source)
        runner = create_runner(tasker, workflow)
        with pytest.raises(PluginFailedException) as exc:
            runner.run()
        assert "plugin 'koji_promote' raised an exception: RuntimeError" in str(exc)

    @pytest.mark.parametrize(('koji_task_id', 'expect_success'), [
        (12345, True),
        ('x', False),
    ])
    def test_koji_promote_log_task_id(self, tmpdir, monkeypatch, os_env,
                                      caplog, koji_task_id, expect_success):
        session = MockedClientSession('')
        tasker, workflow = mock_environment(tmpdir,
                                            session=session,
                                            name='ns/name',
                                            version='1.0',
                                            release='1')
        runner = create_runner(tasker, workflow)

        monkeypatch.setenv("BUILD", json.dumps({
            'metadata': {
                'creationTimestamp': '2015-07-27T09:24:00Z',
                'namespace': NAMESPACE,
                'name': BUILD_ID,
                'labels': {
                    'koji-task-id': str(koji_task_id),
                },
            }
        }))

        runner.run()
        metadata = session.metadata
        assert 'build' in metadata
        build = metadata['build']
        assert isinstance(build, dict)
        assert 'extra' in build
        extra = build['extra']
        assert isinstance(extra, dict)

        if expect_success:
            assert "Koji Task ID {}".format(koji_task_id) in caplog.text()

            assert 'container_koji_task_id' in extra
            extra_koji_task_id = extra['container_koji_task_id']
            assert isinstance(extra_koji_task_id, int)
            assert extra_koji_task_id == koji_task_id
        else:
            assert "invalid task ID" in caplog.text()
            assert 'container_koji_task_id' not in extra

    @pytest.mark.parametrize('params', [
        {
            'should_raise': False,
            'principal': None,
            'keytab': None,
        },

        {
            'should_raise': False,
            'principal': 'principal@EXAMPLE.COM',
            'keytab': 'FILE:/var/run/secrets/mysecret',
        },

        {
            'should_raise': True,
            'principal': 'principal@EXAMPLE.COM',
            'keytab': None,
        },

        {
            'should_raise': True,
            'principal': None,
            'keytab': 'FILE:/var/run/secrets/mysecret',
        },
    ])
    def test_koji_promote_krb_args(self, tmpdir, params, os_env):
        session = MockedClientSession('')
        expectation = flexmock(session).should_receive('krb_login').and_return(True)
        name = 'name'
        version = '1.0'
        release = '1'
        tasker, workflow = mock_environment(tmpdir,
                                            session=session,
                                            name=name,
                                            version=version,
                                            release=release)
        runner = create_runner(tasker, workflow,
                               principal=params['principal'],
                               keytab=params['keytab'])

        if params['should_raise']:
            expectation.never()
            with pytest.raises(PluginFailedException):
                runner.run()
        else:
            expectation.once()
            runner.run()

    def test_koji_promote_krb_fail(self, tmpdir, os_env):
        session = MockedClientSession('')
        (flexmock(session)
            .should_receive('krb_login')
            .and_raise(RuntimeError)
            .once())
        tasker, workflow = mock_environment(tmpdir,
                                            session=session,
                                            name='ns/name',
                                            version='1.0',
                                            release='1')
        runner = create_runner(tasker, workflow)
        with pytest.raises(PluginFailedException):
            runner.run()

    def test_koji_promote_ssl_fail(self, tmpdir, os_env):
        session = MockedClientSession('')
        (flexmock(session)
            .should_receive('ssl_login')
            .and_raise(RuntimeError)
            .once())
        tasker, workflow = mock_environment(tmpdir,
                                            session=session,
                                            name='ns/name',
                                            version='1.0',
                                            release='1')
        runner = create_runner(tasker, workflow, ssl_certs=True)
        with pytest.raises(PluginFailedException):
            runner.run()

    @pytest.mark.parametrize('fail_method', [
        'get_build_logs',
        'get_pod_for_build',
    ])
    def test_koji_promote_osbs_fail(self, tmpdir, os_env, fail_method):
        tasker, workflow = mock_environment(tmpdir,
                                            name='name',
                                            version='1.0',
                                            release='1')
        (flexmock(OSBS)
            .should_receive(fail_method)
            .and_raise(OsbsException))

        runner = create_runner(tasker, workflow)
        runner.run()

    @staticmethod
    def check_components(components):
        assert isinstance(components, list)
        assert len(components) > 0
        for component_rpm in components:
            assert isinstance(component_rpm, dict)
            assert set(component_rpm.keys()) == set([
                'type',
                'name',
                'version',
                'release',
                'epoch',
                'arch',
                'sigmd5',
                'signature',
            ])

            assert component_rpm['type'] == 'rpm'
            assert component_rpm['name']
            assert is_string_type(component_rpm['name'])
            assert component_rpm['name'] != 'gpg-pubkey'
            assert component_rpm['version']
            assert is_string_type(component_rpm['version'])
            assert component_rpm['release']
            epoch = component_rpm['epoch']
            assert epoch is None or isinstance(epoch, int)
            assert is_string_type(component_rpm['arch'])
            assert component_rpm['signature'] != '(none)'

    def validate_buildroot(self, buildroot):
        assert isinstance(buildroot, dict)

        assert set(buildroot.keys()) == set([
            'id',
            'host',
            'content_generator',
            'container',
            'tools',
            'components',
            'extra',
        ])

        host = buildroot['host']
        assert isinstance(host, dict)
        assert set(host.keys()) == set([
            'os',
            'arch',
        ])

        assert host['os']
        assert is_string_type(host['os'])
        assert host['arch']
        assert is_string_type(host['arch'])
        assert host['arch'] != 'amd64'

        content_generator = buildroot['content_generator']
        assert isinstance(content_generator, dict)
        assert set(content_generator.keys()) == set([
            'name',
            'version',
        ])

        assert content_generator['name']
        assert is_string_type(content_generator['name'])
        assert content_generator['version']
        assert is_string_type(content_generator['version'])

        container = buildroot['container']
        assert isinstance(container, dict)
        assert set(container.keys()) == set([
            'type',
            'arch',
        ])

        assert container['type'] == 'docker'
        assert container['arch']
        assert is_string_type(container['arch'])

        assert isinstance(buildroot['tools'], list)
        assert len(buildroot['tools']) > 0
        for tool in buildroot['tools']:
            assert isinstance(tool, dict)
            assert set(tool.keys()) == set([
                'name',
                'version',
            ])

            assert tool['name']
            assert is_string_type(tool['name'])
            assert tool['version']
            assert is_string_type(tool['version'])

        self.check_components(buildroot['components'])

        extra = buildroot['extra']
        assert isinstance(extra, dict)
        assert set(extra.keys()) == set([
            'osbs',
        ])

        assert 'osbs' in extra
        osbs = extra['osbs']
        assert isinstance(osbs, dict)
        assert set(osbs.keys()) == set([
            'build_id',
            'builder_image_id',
        ])

        assert is_string_type(osbs['build_id'])
        assert is_string_type(osbs['builder_image_id'])

    def validate_output(self, output, metadata_only, has_config,
                        expect_digest):
        if metadata_only:
            mdonly = set()
        else:
            mdonly = set(['metadata_only'])

        assert isinstance(output, dict)
        assert 'buildroot_id' in output
        assert 'filename' in output
        assert output['filename']
        assert is_string_type(output['filename'])
        assert 'filesize' in output
        assert int(output['filesize']) > 0 or metadata_only
        assert 'arch' in output
        assert output['arch']
        assert is_string_type(output['arch'])
        assert 'checksum' in output
        assert output['checksum']
        assert is_string_type(output['checksum'])
        assert 'checksum_type' in output
        assert output['checksum_type'] == 'md5'
        assert is_string_type(output['checksum_type'])
        assert 'type' in output
        if output['type'] == 'log':
            assert set(output.keys()) == set([
                'buildroot_id',
                'filename',
                'filesize',
                'arch',
                'checksum',
                'checksum_type',
                'type',
                'metadata_only',  # only when True
            ]) - mdonly
            assert output['arch'] == 'noarch'
        else:
            assert set(output.keys()) == set([
                'buildroot_id',
                'filename',
                'filesize',
                'arch',
                'checksum',
                'checksum_type',
                'type',
                'components',
                'extra',
                'metadata_only',  # only when True
            ]) - mdonly
            assert output['type'] == 'docker-image'
            assert is_string_type(output['arch'])
            assert output['arch'] != 'noarch'
            assert output['arch'] in output['filename']
            self.check_components(output['components'])

            extra = output['extra']
            assert isinstance(extra, dict)
            assert set(extra.keys()) == set([
                'image',
                'docker',
            ])

            image = extra['image']
            assert isinstance(image, dict)
            assert set(image.keys()) == set([
                'arch',
            ])

            assert image['arch'] == output['arch']  # what else?

            assert 'docker' in extra
            docker = extra['docker']
            assert isinstance(docker, dict)
            expected_keys_set = set([
                'parent_id',
                'id',
                'repositories',
                'tags',
            ])
            if has_config:
                expected_keys_set.add('config')
            assert set(docker.keys()) == expected_keys_set

            assert is_string_type(docker['parent_id'])
            assert is_string_type(docker['id'])
            repositories = docker['repositories']
            assert isinstance(repositories, list)
            repositories_digest = list(filter(lambda repo: '@sha256' in repo, repositories))
            repositories_tag = list(filter(lambda repo: '@sha256' not in repo, repositories))

            assert len(repositories_tag) == 1
            if expect_digest:
                assert len(repositories_digest) == 1
            else:
                assert not repositories_digest

            # check for duplicates
            assert sorted(repositories_tag) == sorted(set(repositories_tag))
            assert sorted(repositories_digest) == sorted(set(repositories_digest))

            for repository in repositories_tag:
                assert is_string_type(repository)
                image = ImageName.parse(repository)
                assert image.registry
                assert image.namespace
                assert image.repo
                assert image.tag and image.tag != 'latest'

            if expect_digest:
                digest_pullspec = image.to_str(tag=False) + '@' + fake_digest(image)
                assert digest_pullspec in repositories_digest

            tags = docker['tags']
            assert isinstance(tags, list)
            assert all(is_string_type(tag) for tag in tags)

            if has_config:
                config = docker['config']
                assert isinstance(config, dict)
                assert 'container_config' not in [x.lower() for x in config.keys()]
                assert all(is_string_type(entry) for entry in config)

    def test_koji_promote_import_fail(self, tmpdir, os_env, caplog):
        session = MockedClientSession('')
        (flexmock(session)
            .should_receive('CGImport')
            .and_raise(RuntimeError))
        name = 'ns/name'
        version = '1.0'
        release = '1'
        target = 'images-docker-candidate'
        tasker, workflow = mock_environment(tmpdir,
                                            name=name,
                                            version=version,
                                            release=release,
                                            session=session)
        runner = create_runner(tasker, workflow, target=target)
        with pytest.raises(PluginFailedException):
            runner.run()

        assert 'metadata:' in caplog.text()

    @pytest.mark.parametrize('task_states', [
        ['FREE', 'ASSIGNED', 'FAILED'],
        ['CANCELED'],
        [None],
    ])
    def test_koji_promote_tag_fail(self, tmpdir, task_states, os_env):
        session = MockedClientSession('', task_states=task_states)
        name = 'ns/name'
        version = '1.0'
        release = '1'
        target = 'images-docker-candidate'
        tasker, workflow = mock_environment(tmpdir,
                                            name=name,
                                            version=version,
                                            release=release,
                                            session=session)
        runner = create_runner(tasker, workflow, target=target)
        with pytest.raises(PluginFailedException):
            runner.run()

    @pytest.mark.parametrize(('task_id', 'expect_success'), [
        (1234, True),
        ('x', False),
    ])
    def test_koji_promote_filesystem_koji_task_id(self, tmpdir, os_env, caplog,
                                                  task_id, expect_success):
        session = MockedClientSession('')
        tasker, workflow = mock_environment(tmpdir,
                                            name='ns/name',
                                            version='1.0',
                                            release='1',
                                            session=session)
        workflow.prebuild_results[AddFilesystemPlugin.key] = {
            'base-image-id': 'abcd',
            'filesystem-koji-task-id': task_id,
        }
        runner = create_runner(tasker, workflow)
        runner.run()

        data = session.metadata
        assert 'build' in data
        build = data['build']
        assert isinstance(build, dict)
        assert 'extra' in build
        extra = build['extra']
        assert isinstance(extra, dict)

        if expect_success:
            assert 'filesystem_koji_task_id' in extra
            filesystem_koji_task_id = extra['filesystem_koji_task_id']
            assert isinstance(filesystem_koji_task_id, int)
            assert filesystem_koji_task_id == task_id
        else:
            assert 'invalid task ID' in caplog.text()
            assert 'filesystem_koji_task_id' not in extra

    def test_koji_promote_filesystem_koji_task_id_missing(self, tmpdir, os_env,
                                                          caplog):
        session = MockedClientSession('')
        tasker, workflow = mock_environment(tmpdir,
                                            name='ns/name',
                                            version='1.0',
                                            release='1',
                                            session=session)
        workflow.prebuild_results[AddFilesystemPlugin.key] = {
            'base-image-id': 'abcd',
        }
        runner = create_runner(tasker, workflow)
        runner.run()

        data = session.metadata
        assert 'build' in data
        build = data['build']
        assert isinstance(build, dict)
        assert 'extra' in build
        extra = build['extra']
        assert isinstance(extra, dict)
        assert 'filesystem_koji_task_id' not in extra
        assert AddFilesystemPlugin.key in caplog.text()

    @pytest.mark.parametrize('additional_tags', [
        None,
        ['3.2'],
    ])
    def test_koji_promote_image_tags(self, tmpdir, os_env, additional_tags):
        session = MockedClientSession('')
        version = '3.2.1'
        release = '4'
        tasker, workflow = mock_environment(tmpdir,
                                            name='ns/name',
                                            version=version,
                                            release=release,
                                            session=session,
                                            additional_tags=additional_tags)
        runner = create_runner(tasker, workflow)
        runner.run()

        data = session.metadata

        # Find the docker output section
        outputs = data['output']
        docker_outputs = [output for output in outputs
                          if output['type'] == 'docker-image']
        assert len(docker_outputs) == 1
        output = docker_outputs[0]

        # Check the extra.docker.tags field
        docker = output['extra']['docker']
        assert isinstance(docker, dict)
        assert 'tags' in docker
        tags = docker['tags']
        assert isinstance(tags, list)
        expected_tags = set([version,
                             "{}-{}".format(version, release),
                             'latest'])
        if additional_tags:
            expected_tags.update(additional_tags)

        assert set(tags) == expected_tags

    @pytest.mark.parametrize(('apis',
                              'docker_registry',
                              'pulp_registries',
                              'metadata_only',
                              'blocksize',
                              'target'), [
        ('v1-only',
         False,
         1,
         False,
         None,
         'images-docker-candidate'),

        ('v1+v2',
         True,
         2,
         False,
         10485760,
         None),

        ('v2-only',
         True,
         1,
         True,
         None,
         None),

        ('v1+v2',
         True,
         0,
         False,
         10485760,
         None),

    ])
    @pytest.mark.parametrize(('has_config', 'is_autorebuild'), [
        (True,
         True),
        (False,
         False),
    ])
    @pytest.mark.parametrize('tag_later', (True, False))
    def test_koji_promote_success(self, tmpdir, apis, docker_registry,
                                  pulp_registries, metadata_only, blocksize,
                                  target, os_env, has_config, is_autorebuild,
                                  tag_later):
        session = MockedClientSession('')
        # When target is provided koji build will always be tagged,
        # either by koji_promote or koji_tag_build.
        (flexmock(session)
            .should_call('tagBuild')
            .with_args('images-candidate', '123')
            .times(1 if target else 0))

        component = 'component'
        name = 'ns/name'
        version = '1.0'
        release = '1'

        if has_config and not docker_registry:
            # Not a valid combination
            has_config = False

        tasker, workflow = mock_environment(tmpdir,
                                            session=session,
                                            name=name,
                                            component=component,
                                            version=version,
                                            release=release,
                                            docker_registry=docker_registry,
                                            pulp_registries=pulp_registries,
                                            blocksize=blocksize,
                                            has_config=has_config)
        workflow.prebuild_results[CheckAndSetRebuildPlugin.key] = is_autorebuild
        runner = create_runner(tasker, workflow, metadata_only=metadata_only,
                               blocksize=blocksize, target=target,
                               tag_later=tag_later)
        result = runner.run()

        # Look at koji_tag_build result for determining which plugin
        # performing koji build taggging
        if tag_later and target:
            assert result[KojiTagBuildPlugin.key] == 'images-candidate'
        else:
            assert result.get(KojiTagBuildPlugin.key) is None

        data = session.metadata
        if metadata_only:
            mdonly = set()
        else:
            mdonly = set(['metadata_only'])

        assert set(data.keys()) == set([
            'metadata_version',
            'build',
            'buildroots',
            'output',
        ])

        assert data['metadata_version'] in ['0', 0]

        build = data['build']
        assert isinstance(build, dict)

        buildroots = data['buildroots']
        assert isinstance(buildroots, list)
        assert len(buildroots) > 0

        output_files = data['output']
        assert isinstance(output_files, list)

        assert set(build.keys()) == set([
            'name',
            'version',
            'release',
            'source',
            'start_time',
            'end_time',
            'extra',          # optional but always supplied
            'metadata_only',  # only when True
        ]) - mdonly

        assert build['name'] == component
        assert build['version'] == version
        assert build['release'] == release
        assert build['source'] == 'git://hostname/path#123456'
        start_time = build['start_time']
        assert isinstance(start_time, int) and start_time
        end_time = build['end_time']
        assert isinstance(end_time, int) and end_time
        if metadata_only:
            assert isinstance(build['metadata_only'], bool)
            assert build['metadata_only']

        extra = build['extra']
        assert isinstance(extra, dict)
        assert 'image' in extra
        image = extra['image']
        assert isinstance(image, dict)
        assert 'autorebuild' in image
        autorebuild = image['autorebuild']
        assert isinstance(autorebuild, bool)
        assert autorebuild == is_autorebuild

        for buildroot in buildroots:
            self.validate_buildroot(buildroot)

            # Unique within buildroots in this metadata
            assert len([b for b in buildroots
                        if b['id'] == buildroot['id']]) == 1

        for output in output_files:
            self.validate_output(output, metadata_only, has_config,
                                 expect_digest=docker_registry)
            buildroot_id = output['buildroot_id']

            # References one of the buildroots
            assert len([buildroot for buildroot in buildroots
                        if buildroot['id'] == buildroot_id]) == 1

            if metadata_only:
                assert isinstance(output['metadata_only'], bool)
                assert output['metadata_only']

        files = session.uploaded_files

        # There should be a file in the list for each output
        # except for metadata-only imports, in which case there
        # will be no upload for the image itself
        assert isinstance(files, list)
        expected_uploads = len(output_files)
        if metadata_only:
            expected_uploads -= 1

        assert len(files) == expected_uploads

        # The correct blocksize argument should have been used
        if blocksize is not None:
            assert blocksize == session.blocksize

        build_id = runner.plugins_results[KojiPromotePlugin.key]
        assert build_id == "123"

        if target is not None:
            assert session.build_tags[build_id] == session.DEST_TAG
            assert session.tag_task_state == 'CLOSED'

    @pytest.mark.parametrize(('primary', 'unique', 'invalid'), [
        (True, True, False),
        (True, False, False),
        (False, True, False),
        (False, False, True),
    ])
    def test_koji_promote_pullspec(self, tmpdir, os_env, primary, unique, invalid):
        session = MockedClientSession('')
        name = 'ns/name'
        version = '1.0'
        release = '1'
        tasker, workflow = mock_environment(tmpdir,
                                            session=session,
                                            name=name,
                                            version=version,
                                            release=release,
                                            pulp_registries=1,
                                            )
        if not primary:
            workflow.tag_conf._primary_images = []
        if not unique:
            workflow.tag_conf._unique_images = []

        runner = create_runner(tasker, workflow)

        if invalid:
            with pytest.raises(PluginFailedException):
                runner.run()
            return

        runner.run()

        docker_outputs = [
            output
            for output in session.metadata['output']
            if output['type'] == 'docker-image'
        ]
        assert len(docker_outputs) == 1
        docker_output = docker_outputs[0]

        pullspecs = [
            repo
            for repo in docker_output['extra']['docker']['repositories']
            if '@sha256' not in repo
        ]
        assert len(pullspecs) == 1
        pullspec = pullspecs[0]

        if primary:
            nvr_tag = '{}:{}-{}'.format(name, version, release)
            assert pullspec.endswith(nvr_tag)
        else:
            assert pullspec.endswith('-timestamp')

    def test_koji_promote_without_build_info(self, tmpdir, os_env):

        class LegacyCGImport(MockedClientSession):

            def CGImport(self, *args, **kwargs):
                super(LegacyCGImport, self).CGImport(*args, **kwargs)
                return

        session = LegacyCGImport('')
        name = 'ns/name'
        version = '1.0'
        release = '1'
        tasker, workflow = mock_environment(tmpdir,
                                            session=session,
                                            name=name,
                                            version=version,
                                            release=release)
        runner = create_runner(tasker, workflow)
        runner.run()

        assert runner.plugins_results[KojiPromotePlugin.key] is None

    @pytest.mark.parametrize('expect_result', [
        'empty_config',
        'no_help_file',
        'skip',
        'pass',
        'unknown_status'
    ])
    def test_koji_promote_add_help(self, tmpdir, os_env, expect_result):
        session = MockedClientSession('')
        tasker, workflow = mock_environment(tmpdir,
                                            name='ns/name',
                                            version='1.0',
                                            release='1',
                                            session=session)

        if expect_result == 'pass':
            workflow.prebuild_results[AddHelpPlugin.key] = {
                'help_file': 'foo.md',
                'status': AddHelpPlugin.HELP_GENERATED
            }

        elif expect_result == 'empty_config':
            workflow.prebuild_results[AddHelpPlugin.key] = {
                'help_file': 'help.md',
                'status': AddHelpPlugin.HELP_GENERATED
            }

        elif expect_result == 'no_help_file':
            workflow.prebuild_results[AddHelpPlugin.key] = {
                'help_file': 'foo.md',
                'status': AddHelpPlugin.NO_HELP_FILE_FOUND
            }

        elif expect_result == 'unknown_status':
            workflow.prebuild_results[AddHelpPlugin.key] = {
                'help_file': 'foo.md',
                'status': 99999
            }

        elif expect_result == 'skip':
            workflow.prebuild_results[AddHelpPlugin.key] = None

        runner = create_runner(tasker, workflow)
        runner.run()

        data = session.metadata
        assert 'build' in data
        build = data['build']
        assert isinstance(build, dict)
        assert 'extra' in build
        extra = build['extra']
        assert isinstance(extra, dict)
        assert 'image' in extra
        image = extra['image']
        assert isinstance(image, dict)

        if expect_result == 'pass':
            assert 'help' in image.keys()
            assert image['help'] == 'foo.md'
        elif expect_result == 'empty_config':
            assert 'help' in image.keys()
            assert image['help'] == 'help.md'
        elif expect_result == 'no_help_file':
            assert 'help' in image.keys()
            assert image['help'] is None
        elif expect_result in ['skip', 'unknown_status']:
            assert 'help' not in image.keys()

    @pytest.mark.parametrize('logs_return_bytes', [
        True,
        False,
    ])
    def test_koji_promote_logs(self, tmpdir, os_env, logs_return_bytes):
        tasker, workflow = mock_environment(tmpdir,
                                            name='name',
                                            version='1.0',
                                            release='1',
                                            logs_return_bytes=logs_return_bytes)
        runner = create_runner(tasker, workflow)
        runner.run()
