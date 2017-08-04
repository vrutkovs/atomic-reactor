"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""

from __future__ import unicode_literals

import os
import sys

from atomic_reactor.core import DockerTasker
from atomic_reactor.inner import DockerBuildWorkflow
from atomic_reactor.plugin import PostBuildPluginsRunner
from atomic_reactor.util import ImageName
try:
    if sys.version_info.major > 2:
        # importing dockpulp in Python 3 causes SyntaxError
        raise ImportError

    import dockpulp
    from atomic_reactor.plugins.post_push_to_pulp import PulpPushPlugin
except (ImportError):
    dockpulp = None

import subprocess

import pytest
from flexmock import flexmock
from tests.constants import INPUT_IMAGE, SOURCE, MOCK
if MOCK:
    from tests.docker_mock import mock_docker


class X(object):
    image_id = INPUT_IMAGE
    git_dockerfile_path = None
    git_path = None
    base_image = ImageName(repo="qwe", tag="asd")


def prepare(check_repo_retval=0, existing_layers=[],
            subprocess_exceptions=False,
            conf=None):
    if MOCK:
        mock_docker()
    tasker = DockerTasker()
    workflow = DockerBuildWorkflow(SOURCE, "test-image",
                                   postbuild_plugins=conf)
    setattr(workflow, 'builder', X())
    setattr(workflow.builder, 'source', X())
    setattr(workflow.builder.source, 'dockerfile_path', None)
    setattr(workflow.builder.source, 'path', None)
    setattr(workflow, 'tag_conf', X())
    setattr(workflow.tag_conf, 'images', [ImageName(repo="image-name1"),
                                          ImageName(namespace="prefix",
                                                    repo="image-name2"),
                                          ImageName(repo="image-name3", tag="asd")])

    # Mock dockpulp and docker
    dockpulp.Pulp = flexmock(dockpulp.Pulp)
    dockpulp.Pulp.registry = 'registry.example.com'
    (flexmock(dockpulp.imgutils).should_receive('get_metadata')
     .with_args(object)
     .and_return([{'id': 'foo'}]))
    (flexmock(dockpulp.imgutils).should_receive('get_manifest')
     .with_args(object)
     .and_return([{'id': 'foo'}]))
    (flexmock(dockpulp.imgutils).should_receive('get_versions')
     .with_args(object)
     .and_return({'id': '1.6.0'}))
    (flexmock(dockpulp.imgutils).should_receive('check_repo')
     .and_return(check_repo_retval))
    (flexmock(dockpulp.Pulp)
     .should_receive('set_certs')
     .with_args(object, object))
    (flexmock(dockpulp.Pulp)
     .should_receive('getRepos')
     .with_args(list, fields=list)
     .and_return([
         {"id": "redhat-image-name1"},
         {"id": "redhat-prefix-image-name2"}
      ]))
    (flexmock(dockpulp.Pulp)
     .should_receive('createRepo'))
    (flexmock(dockpulp.Pulp)
     .should_receive('upload')
     .with_args(unicode)).at_most().once()
    (flexmock(dockpulp.Pulp)
     .should_receive('copy')
     .with_args(unicode, unicode))
    (flexmock(dockpulp.Pulp)
     .should_receive('updateRepo')
     .with_args(unicode, dict))
    (flexmock(dockpulp.Pulp)
     .should_receive('crane')
     .with_args(list, wait=True)
     .and_return([2, 3, 4]))
    (flexmock(dockpulp.Pulp)
     .should_receive('')
     .with_args(object, object)
     .and_return([1, 2, 3]))
    (flexmock(dockpulp.Pulp)
     .should_receive('watch_tasks')
     .with_args(list))
    if existing_layers is not None:
        (flexmock(dockpulp.Pulp).should_receive('getImageIdsExist')
         .with_args(list)
         .and_return(existing_layers))
    if subprocess_exceptions:
        (flexmock(subprocess)
         .should_receive("check_call")
         .and_raise(Exception))

    mock_docker()
    return tasker, workflow


@pytest.mark.skipif(dockpulp is None,
                    reason='dockpulp module not available')
@pytest.mark.parametrize(("existing_layers", "should_raise", "subprocess_exceptions"), [
    (None, True, False),               # mock dockpulp without getImageIdsExist method
    ([], True, False),                 # this will trigger remove dedup layers and pass
    (['no-such-layer'], True, False),  # no such layer - tar command will fail
    ([], True, True),                  # all subprocess.check_call will fail
])
def test_pulp_dedup_layers(
        tmpdir, existing_layers, should_raise, monkeypatch, subprocess_exceptions):
    tasker, workflow = prepare(
        check_repo_retval=0,
        existing_layers=existing_layers,
        subprocess_exceptions=subprocess_exceptions)
    monkeypatch.setenv('SOURCE_SECRET_PATH', str(tmpdir))
    with open(os.path.join(str(tmpdir), "pulp.cer"), "wt") as cer:
        cer.write("pulp certificate\n")
    with open(os.path.join(str(tmpdir), "pulp.key"), "wt") as key:
        key.write("pulp key\n")

    runner = PostBuildPluginsRunner(tasker, workflow, [{
        'name': PulpPushPlugin.key,
        'args': {
            'pulp_registry_name': 'test'
        }}])

    runner.run()
    assert PulpPushPlugin.key is not None
    top_layer, crane_images = workflow.postbuild_results[PulpPushPlugin.key]
    images = [i.to_str() for i in crane_images]
    assert "registry.example.com/image-name1:latest" in images
    assert "registry.example.com/prefix/image-name2:latest" in images
    assert "registry.example.com/image-name3:asd" in images
    assert top_layer == 'foo'


@pytest.mark.skipif(dockpulp is None,
                    reason='dockpulp module not available')
@pytest.mark.parametrize(("check_repo_retval", "should_raise"), [
    (3, True),
    (2, True),
    (1, True),
    (0, False),
])
def test_pulp_source_secret(tmpdir, check_repo_retval, should_raise, monkeypatch):
    tasker, workflow = prepare(check_repo_retval=check_repo_retval)
    monkeypatch.setenv('SOURCE_SECRET_PATH', str(tmpdir))
    with open(os.path.join(str(tmpdir), "pulp.cer"), "wt") as cer:
        cer.write("pulp certificate\n")
    with open(os.path.join(str(tmpdir), "pulp.key"), "wt") as key:
        key.write("pulp key\n")

    runner = PostBuildPluginsRunner(tasker, workflow, [{
        'name': PulpPushPlugin.key,
        'args': {
            'pulp_registry_name': 'test'
        }}])

    if should_raise:
        with pytest.raises(Exception):
            runner.run()

        return

    runner.run()
    assert PulpPushPlugin.key is not None
    _, crane_images = workflow.postbuild_results[PulpPushPlugin.key]
    images = [i.to_str() for i in crane_images]
    assert "registry.example.com/image-name1:latest" in images
    assert "registry.example.com/prefix/image-name2:latest" in images
    assert "registry.example.com/image-name3:asd" in images


@pytest.mark.skipif(dockpulp is None,
                    reason='dockpulp module not available')
def test_pulp_service_account_secret(tmpdir, monkeypatch):
    tasker, workflow = prepare()
    monkeypatch.setenv('SOURCE_SECRET_PATH', str(tmpdir) + "/not-used")
    with open(os.path.join(str(tmpdir), "pulp.cer"), "wt") as cer:
        cer.write("pulp certificate\n")
    with open(os.path.join(str(tmpdir), "pulp.key"), "wt") as key:
        key.write("pulp key\n")

    runner = PostBuildPluginsRunner(tasker, workflow, [{
        'name': PulpPushPlugin.key,
        'args': {
            'pulp_registry_name': 'test',
            'pulp_secret_path': str(tmpdir),
        }}])

    runner.run()
    _, crane_images = workflow.postbuild_results[PulpPushPlugin.key]
    images = [i.to_str() for i in crane_images]
    assert "registry.example.com/image-name1:latest" in images
    assert "registry.example.com/prefix/image-name2:latest" in images
    assert "registry.example.com/image-name3:asd" in images


@pytest.mark.skipif(dockpulp is None,
                    reason='dockpulp module not available')
@pytest.mark.parametrize(('before_name', 'after_name', 'should_publish'), [
    ('foo', 'foo', True),
    ('pulp_sync', 'foo', True),
    ('foo', 'pulp_sync', False),
])
def test_pulp_publish_only_without_sync(before_name, after_name,
                                        should_publish, caplog):
    conf = [
        {
            'name': before_name,
            'args': {
                'pulp_registry_name': 'foo',
                'docker_registry': 'bar',
            },
        },
        {
            'name': PulpPushPlugin.key,
            'args': {
                'pulp_registry_name': 'test'
            },
        },
        {
            'name': after_name,
            'args': {
                'pulp_registry_name': 'foo',
                'docker_registry': 'bar',
            },
        },
    ]

    tasker, workflow = prepare(conf=conf)
    plugin = PulpPushPlugin(tasker, workflow, 'pulp_registry_name')

    expectation = flexmock(dockpulp.Pulp).should_receive('crane')
    if should_publish:
        (expectation
            .once()
            .and_return([]))
    else:
        expectation.never()

    plugin.run()

    if should_publish:
        assert 'to be published' in caplog.text()
    else:
        assert 'publishing deferred' in caplog.text()
