"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""

from __future__ import unicode_literals

import json
import os
import tempfile
import pytest
import requests
import responses
from requests.exceptions import ConnectionError
import six

from tempfile import mkdtemp
from textwrap import dedent
from flexmock import flexmock

from collections import OrderedDict
import docker
from atomic_reactor.inner import DockerBuildWorkflow
from atomic_reactor.util import (ImageName, wait_for_command, clone_git_repo,
                                 LazyGit, figure_out_dockerfile,
                                 render_yum_repo, process_substitutions,
                                 get_checksums, print_version_of_tools,
                                 get_version_of_tools, get_preferred_label_key,
                                 human_size, CommandResult,
                                 get_manifest_digests, ManifestDigest,
                                 get_build_json, is_scratch_build, df_parser,
                                 are_plugins_in_order, LabelFormatter,
                                 get_manifest_media_type)
from atomic_reactor import util
from tests.constants import DOCKERFILE_GIT, INPUT_IMAGE, MOCK, DOCKERFILE_SHA1, MOCK_SOURCE
from atomic_reactor.constants import INSPECT_CONFIG

from tests.util import requires_internet

if MOCK:
    from tests.docker_mock import mock_docker

TEST_DATA = {
    "repository.com/image-name": ImageName(registry="repository.com", repo="image-name"),
    "repository.com/prefix/image-name:1": ImageName(registry="repository.com",
                                                    namespace="prefix",
                                                    repo="image-name", tag="1"),
    "repository.com/prefix/image-name@sha256:12345": ImageName(registry="repository.com",
                                                               namespace="prefix",
                                                               repo="image-name",
                                                               tag="sha256:12345"),
    "repository.com/prefix/image-name": ImageName(registry="repository.com",
                                                  namespace="prefix",
                                                  repo="image-name"),
    "image-name": ImageName(repo="image-name"),

    "registry:5000/image-name:latest": ImageName(registry="registry:5000",
                                                 repo="image-name", tag="latest"),
    "registry:5000/image-name@sha256:12345": ImageName(registry="registry:5000",
                                                       repo="image-name", tag="sha256:12345"),
    "registry:5000/image-name": ImageName(registry="registry:5000", repo="image-name"),

    "fedora:20": ImageName(repo="fedora", tag="20"),
    "fedora@sha256:12345": ImageName(repo="fedora", tag="sha256:12345"),

    "prefix/image-name:1": ImageName(namespace="prefix", repo="image-name", tag="1"),
    "prefix/image-name@sha256:12345": ImageName(namespace="prefix", repo="image-name",
                                                tag="sha256:12345"),

    "library/fedora:20": ImageName(namespace="library", repo="fedora", tag="20"),
    "library/fedora@sha256:12345": ImageName(namespace="library", repo="fedora",
                                             tag="sha256:12345"),
}


def test_image_name_parse():
    for inp, parsed in TEST_DATA.items():
        assert ImageName.parse(inp) == parsed


def test_image_name_format():
    for expected, image_name in TEST_DATA.items():
        assert image_name.to_str() == expected


def test_image_name_comparison():
    # make sure that both "==" and "!=" are implemented right on both Python major releases
    i1 = ImageName(registry='foo.com', namespace='spam', repo='bar', tag='1')
    i2 = ImageName(registry='foo.com', namespace='spam', repo='bar', tag='1')
    assert i1 == i2
    assert not i1 != i2

    i2 = ImageName(registry='foo.com', namespace='spam', repo='bar', tag='2')
    assert not i1 == i2
    assert i1 != i2


def test_wait_for_command():
    if MOCK:
        mock_docker()

    d = docker.APIClient()
    logs_gen = d.pull(INPUT_IMAGE, decode=True, stream=True)
    assert wait_for_command(logs_gen) is not None


@requires_internet
def test_clone_git_repo(tmpdir):
    tmpdir_path = str(tmpdir.realpath())
    commit_id = clone_git_repo(DOCKERFILE_GIT, tmpdir_path)
    assert commit_id is not None
    assert len(commit_id) == 40  # current git hashes are this long
    assert os.path.isdir(os.path.join(tmpdir_path, '.git'))


class TestCommandResult(object):
    @pytest.mark.parametrize(('item', 'expected'), [
        ({"stream": "Step 0 : FROM ebbc51b7dfa5bcd993a[...]"},
         "Step 0 : FROM ebbc51b7dfa5bcd993a[...]"),

        ('this is not valid JSON',
         'this is not valid JSON'),
    ])
    def test_parse_item(self, item, expected):
        cr = CommandResult()
        cr.parse_item(item)
        assert cr.logs == [expected]


@requires_internet
def test_clone_git_repo_by_sha1(tmpdir):
    tmpdir_path = str(tmpdir.realpath())
    commit_id = clone_git_repo(DOCKERFILE_GIT, tmpdir_path, commit=DOCKERFILE_SHA1)
    assert commit_id is not None
    print(six.text_type(commit_id))
    print(commit_id)
    assert six.text_type(commit_id, encoding="ascii") == six.text_type(DOCKERFILE_SHA1)
    assert len(commit_id) == 40  # current git hashes are this long
    assert os.path.isdir(os.path.join(tmpdir_path, '.git'))


@requires_internet
def test_figure_out_dockerfile(tmpdir):
    tmpdir_path = str(tmpdir.realpath())
    clone_git_repo(DOCKERFILE_GIT, tmpdir_path)
    path, dir = figure_out_dockerfile(tmpdir_path)
    assert os.path.isfile(path)
    assert os.path.isdir(dir)


@requires_internet
def test_lazy_git():
    lazy_git = LazyGit(git_url=DOCKERFILE_GIT)
    with lazy_git:
        assert lazy_git.git_path is not None
        assert lazy_git.commit_id is not None
        assert len(lazy_git.commit_id) == 40  # current git hashes are this long


@requires_internet
def test_lazy_git_with_tmpdir(tmpdir):
    t = str(tmpdir.realpath())
    lazy_git = LazyGit(git_url=DOCKERFILE_GIT, tmpdir=t)
    assert lazy_git._tmpdir == t
    assert lazy_git.git_path is not None
    assert lazy_git.commit_id is not None
    assert len(lazy_git.commit_id) == 40  # current git hashes are this long


def test_render_yum_repo_unicode():
    yum_repo = OrderedDict((
        ("name", "asd"),
        ("baseurl", "http://example.com/$basearch/test.repo"),
        ("enabled", "1"),
        ("gpgcheck", "0"),
    ))
    rendered_repo = render_yum_repo(yum_repo)
    assert rendered_repo == """\
[asd]
name=asd
baseurl=http://example.com/\$basearch/test.repo
enabled=1
gpgcheck=0
"""


@pytest.mark.parametrize('dct, subst, expected', [
    ({'foo': 'bar'}, ['foo=spam'], {'foo': 'spam'}),
    ({'foo': 'bar'}, ['baz=spam'], {'foo': 'bar', 'baz': 'spam'}),
    ({'foo': 'bar'}, ['foo.bar=spam'], {'foo': {'bar': 'spam'}}),
    ({'foo': 'bar'}, ['spam.spam=spam'], {'foo': 'bar', 'spam': {'spam': 'spam'}}),


    ({'x_plugins': [{'name': 'a', 'args': {'b': 'c'}}]}, {'x_plugins.a.b': 'd'},
        {'x_plugins': [{'name': 'a', 'args': {'b': 'd'}}]}),
    # substituting plugins doesn't add new params
    ({'x_plugins': [{'name': 'a', 'args': {'b': 'c'}}]}, {'x_plugins.a.c': 'd'},
        {'x_plugins': [{'name': 'a', 'args': {'b': 'c'}}]}),
    ({'x_plugins': [{'name': 'a', 'args': {'b': 'c'}}]}, {'x_plugins.X': 'd'},
        ValueError()),
])
def test_process_substitutions(dct, subst, expected):
    if isinstance(expected, Exception):
        with pytest.raises(type(expected)):
            process_substitutions(dct, subst)
    else:
        process_substitutions(dct, subst)
        assert dct == expected


@pytest.mark.parametrize('content, algorithms, expected', [
    (b'abc', ['md5', 'sha256'],
     {'md5sum': '900150983cd24fb0d6963f7d28e17f72',
      'sha256sum': 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'}),
    (b'abc', ['md5'], {'md5sum': '900150983cd24fb0d6963f7d28e17f72'}),
    (b'abc', [], {})
])
def test_get_hexdigests(tmpdir, content, algorithms, expected):
    with tempfile.NamedTemporaryFile(dir=str(tmpdir)) as tmpfile:
        tmpfile.write(content)
        tmpfile.flush()

        checksums = get_checksums(tmpfile.name, algorithms)
        assert checksums == expected


def test_get_versions_of_tools():
    response = get_version_of_tools()
    assert isinstance(response, list)
    for t in response:
        assert t["name"]
        assert t["version"]


def test_print_versions_of_tools():
    print_version_of_tools()


@pytest.mark.parametrize('labels, name, expected', [
    ({'name': 'foo', 'Name': 'foo'}, 'name', 'name'),
    ({'name': 'foo', 'Name': 'foo'}, 'Name', 'name'),
    ({'name': 'foo'}, 'Name', 'name'),
    ({'Name': 'foo'}, 'name', 'Name'),
    ({}, 'Name', 'name'),
    ({}, 'foobar', 'foobar')
])
def test_preferred_labels(labels, name, expected):
    result = get_preferred_label_key(labels, name)
    assert result == expected


@pytest.mark.parametrize('size_input,expected', [
    (0, "0.00 B"),
    (1, "1.00 B"),
    (-1, "-1.00 B"),
    (1536, "1.50 KiB"),
    (-1024, "-1.00 KiB"),
    (204800, "200.00 KiB"),
    (6983516, "6.66 MiB"),
    (14355928186, "13.37 GiB"),
    (135734710448947, "123.45 TiB"),
    (1180579814801204129310965, "999.99 ZiB"),
    (1074589982539051580812825722, "888.88 YiB"),
    (4223769947617154742438477168, "3493.82 YiB"),
    (-4223769947617154742438477168, "-3493.82 YiB"),
])
def test_human_size(size_input, expected):
    assert human_size(size_input) == expected


@pytest.mark.parametrize(('version', 'expected'), [
    ('v1', 'application/vnd.docker.distribution.manifest.v1+json'),
    ('v2', 'application/vnd.docker.distribution.manifest.v2+json'),
    ('v2_list', 'application/vnd.docker.distribution.manifest.list.v2+json'),
])
def test_get_manifest_media_type(version, expected):
    assert get_manifest_media_type(version) == expected


@pytest.mark.parametrize('insecure', [
    True,
    False,
])
@pytest.mark.parametrize('versions,require_digest', [
    (('v1', 'v2', 'v2_list'), True),
    (('v1', 'v2', 'v2_list'), False),
    (('v1',), False),
    (('v1',), True),
    (('v2',), False),
    (('v2',), True),
    (tuple(), False),
    (tuple(), True),
    (None, False),
    (None, True),
    (('v2_list',), True),
    (('v2_list',), False),
])
@pytest.mark.parametrize('creds', [
    ('user1', 'pass'),
    (None, 'pass'),
    ('user1', None),
    None,
])
@pytest.mark.parametrize('image,registry,path', [
    ('not-used.com/spam:latest', 'localhost.com',
     '/v2/spam/manifests/latest'),

    ('not-used.com/food/spam:latest', 'http://localhost.com',
     '/v2/food/spam/manifests/latest'),

    ('not-used.com/spam', 'https://localhost.com',
     '/v2/spam/manifests/latest'),
])
@responses.activate
def test_get_manifest_digests(tmpdir, image, registry, insecure, creds,
                              versions, require_digest, path):
    kwargs = {}

    image = ImageName.parse(image)
    kwargs['image'] = image

    if creds:
        temp_dir = mkdtemp(dir=str(tmpdir))
        with open(os.path.join(temp_dir, '.dockercfg'), 'w+') as dockerconfig:
            dockerconfig.write(json.dumps({
                image.registry: {
                    'username': creds[0], 'password': creds[1]
                }
            }))
        kwargs['dockercfg_path'] = temp_dir

    kwargs['registry'] = registry

    if insecure is not None:
        kwargs['insecure'] = insecure

    if versions is not None:
        kwargs['versions'] = versions

    kwargs['require_digest'] = require_digest

    def request_callback(request, all_headers=True):
        if creds and creds[0] and creds[1]:
            assert request.headers['Authorization']

        media_type = request.headers['Accept']
        if media_type.endswith('list.v2+json'):
            digest = 'v2_list-digest'
        elif media_type.endswith('v2+json'):
            digest = 'v2-digest'
        elif media_type.endswith('v1+json'):
            digest = 'v1-digest'
        else:
            raise ValueError('Unexpected media type {}'.format(media_type))

        media_type_prefix = media_type.split('+')[0]
        if all_headers:
            headers = {
                'Content-Type': '{}+jsonish'.format(media_type_prefix),
                'Docker-Content-Digest': digest
            }
        else:
            headers = {}
        return (200, headers, '')

    if registry.startswith('http'):
        url = registry + path
    else:
        # In the insecure case, we should try the https URL, and when that produces
        # an error, fall back to http
        if insecure:
            https_url = 'https://' + registry + path
            responses.add(responses.GET, https_url, body=ConnectionError())
            url = 'http://' + registry + path
        else:
            url = 'https://' + registry + path
    responses.add_callback(responses.GET, url, callback=request_callback)

    expected_versions = versions
    if versions is None:
        # Test default versions value
        expected_versions = ('v1', 'v2')

    expected_result = dict(
        (version, '{}-digest'.format(version))
        for version in expected_versions)

    if expected_versions:
        actual_digests = get_manifest_digests(**kwargs)
        assert actual_digests.v1 == expected_result.get('v1')
        assert actual_digests.v2 == expected_result.get('v2')
        assert actual_digests.v2_list == expected_result.get('v2_list')
    elif require_digest:
        with pytest.raises(RuntimeError):
            get_manifest_digests(**kwargs)
    else:
        get_manifest_digests(**kwargs)


@pytest.mark.parametrize('has_content_type_header', [
    True, False
])
@pytest.mark.parametrize('has_content_digest', [
    True, False
])
@pytest.mark.parametrize('digest_is_v1,can_convert_v2_v1', [
    (True, False),
    (False, True),
    (False, False),
])
def test_get_manifest_digests_missing(tmpdir, has_content_type_header, has_content_digest,
                                      digest_is_v1, can_convert_v2_v1):
    kwargs = {}

    image = ImageName.parse('example.com/spam:latest')
    kwargs['image'] = image

    kwargs['registry'] = 'https://example.com'

    expected_url = 'https://example.com/v2/spam/manifests/latest'

    def custom_get(url, headers, **kwargs):
        assert url == expected_url

        media_type = headers['Accept']
        media_type_prefix = media_type.split('+')[0]

        assert media_type.endswith('v2+json') or media_type.endswith('v1+json')

        # Attempt to simulate how a docker registry behaves:
        #  * If the stored digest is v1, return it
        #  * If the stored digest is v2, and v2 is requested, return it
        #  * If the stored digest is v2, and v1 is requested, try
        #    to convert and return v1 or an error.
        if digest_is_v1:
            digest = 'v1-digest'
            media_type_prefix = media_type_prefix.replace('v2', 'v1', 1)
        else:
            if media_type.endswith('v2+json'):
                digest = 'v2-digest'
            else:
                if not can_convert_v2_v1:
                    response_json = {"errors": [{"code": "MANIFEST_INVALID"}]}
                    response = requests.Response()
                    flexmock(response,
                             status_code=400,
                             content=json.dumps(response_json).encode("utf-8"),
                             headers=headers)

                    return response

                digest = 'v1-converted-digest'

        headers = {}
        if has_content_type_header:
            headers['Content-Type'] = '{}+jsonish'.format(media_type_prefix)
        if has_content_digest:
            headers['Docker-Content-Digest'] = digest

        if media_type_prefix.endswith('v2'):
            response_json = {'schemaVersion': 2,
                             'mediaType': 'application/vnd.docker.distribution.manifest.v2+json'}
        else:
            response_json = {'schemaVersion': 1}

        response = requests.Response()
        flexmock(response,
                 status_code=200,
                 content=json.dumps(response_json).encode("utf-8"),
                 headers=headers)

        return response

    (flexmock(requests)
        .should_receive('get')
        .replace_with(custom_get))

    if digest_is_v1 and not has_content_type_header:
        # v1 manifests don't have a mediaType field, so we can't fall back
        # to looking at the returned manifest to detect the type.
        with pytest.raises(RuntimeError):
            get_manifest_digests(**kwargs)
        return
    else:
        actual_digests = get_manifest_digests(**kwargs)

    if digest_is_v1:
        if has_content_digest:
            assert actual_digests.v1 == 'v1-digest'
        else:
            assert actual_digests.v1 is True
        assert actual_digests.v2 is None
    else:
        if can_convert_v2_v1:
            if has_content_type_header:
                if has_content_digest:
                    assert actual_digests.v1 == 'v1-converted-digest'
                else:
                    assert actual_digests.v1 is True
            else:  # don't even know the response is v1 without Content-Type
                assert actual_digests.v1 is None
        else:
            assert actual_digests.v1 is None
        if has_content_digest:
            assert actual_digests.v2 == 'v2-digest'
        else:
            assert actual_digests.v2 is True


@responses.activate
def test_get_manifest_digests_connection_error(tmpdir):
    # Test that our code to handle falling back from https to http
    # doesn't do anything unexpected when a connection can't be
    # made at all.
    kwargs = {}
    kwargs['image'] = ImageName.parse('example.com/spam:latest')
    kwargs['registry'] = 'https://example.com'

    url = 'https://example.com/v2/spam/manifests/latest'
    responses.add(responses.GET, url, body=ConnectionError())

    with pytest.raises(ConnectionError):
        get_manifest_digests(**kwargs)


@pytest.mark.parametrize('v1,v2,default', [
    ('v1-digest', 'v2-digest', 'v2-digest'),
    ('v1-digest', None, 'v1-digest'),
    (None, 'v2-digest', 'v2-digest'),
    (None, None, None),
])
def test_manifest_digest(v1, v2, default):
    md = ManifestDigest(v1=v1, v2=v2)
    assert md.v1 == v1
    assert md.v2 == v2
    assert md.default == default


@pytest.mark.parametrize('environ,expected', [
    ({'BUILD': '{"foo": "bar"}'}, {'foo': 'bar'}),
    ({}, False),
])
def test_get_build_json(environ, expected):
    flexmock(os, environ=environ)

    if expected:
        assert get_build_json() == {'foo': 'bar'}
    else:
        with pytest.raises(KeyError):
            get_build_json()


@pytest.mark.parametrize('build_json,scratch', [
    ({'metadata': {'labels': {'scratch': True}}}, True),
    ({'metadata': {'labels': {'scratch': False}}}, False),
    ({'metadata': {'labels': {}}}, False),
    ({'metadata': {}}, None),
    ({}, None),
])
def test_is_scratch_build(build_json, scratch):
    flexmock(util).should_receive('get_build_json').and_return(build_json)
    if scratch is None:
        with pytest.raises(KeyError):
            is_scratch_build()
    else:
        assert is_scratch_build() == scratch


def test_df_parser(tmpdir):
    tmpdir_path = str(tmpdir.realpath())
    df = df_parser(tmpdir_path)
    df.lines = [
        "FROM fedora\n",
        "ENV foo=\"bar\"\n",
        "LABEL label=\"foobar barfoo\"\n"
    ]

    assert len(df.envs) == 1
    assert df.envs.get('foo') == 'bar'
    assert len(df.labels) == 1
    assert df.labels.get('label') == 'foobar barfoo'


def test_df_parser_parent_env_arg(tmpdir):
    p_env = {
        "test_env": "first"
    }
    df_content = dedent("""\
        FROM fedora
        ENV foo=bar
        LABEL label="foobar $test_env"
        """)
    df = df_parser(str(tmpdir), parent_env=p_env)
    df.content = df_content
    assert df.labels.get('label') == 'foobar first'


@pytest.mark.parametrize('env_arg', [
    {"test_env": "first"},
    ['test_env=first'],
    ['test_env='],
    ['test_env=--option=first --option=second'],
    ['test_env_first'],
])
def test_df_parser_parent_env_wf(tmpdir, caplog, env_arg):
    df_content = dedent("""\
        FROM fedora
        ENV foo=bar
        LABEL label="foobar $test_env"
        """)
    env_conf = {INSPECT_CONFIG: {"Env": env_arg}}
    workflow = DockerBuildWorkflow(MOCK_SOURCE, 'test-image')
    flexmock(workflow, base_image_inspect=env_conf)
    df = df_parser(str(tmpdir), workflow=workflow)
    df.content = df_content

    if isinstance(env_arg, list) and ('=' not in env_arg[0]):
        expected_log_message = "Unable to parse all of Parent Config ENV"
        assert expected_log_message in [l.getMessage() for l in caplog.records()]
    elif isinstance(env_arg, dict):
        assert df.labels.get('label') == ('foobar ' + env_arg['test_env'])
    else:
        assert df.labels.get('label') == 'foobar ' + env_arg[0].split('=', 1)[1]


@pytest.mark.parametrize(('available', 'requested', 'result'), (
    (['spam', 'bacon', 'eggs'], ['spam'], True),
    (['spam', 'bacon', 'eggs'], ['spam', 'bacon'], True),
    (['spam', 'bacon', 'eggs'], ['spam', 'bacon', 'eggs'], True),
    (['spam', 'bacon', 'eggs'], ['spam', 'eggs'], True),
    (['spam', 'bacon', 'eggs'], ['eggs', 'spam'], False),
    (['spam', 'bacon', 'eggs'], ['spam', 'eggs', 'bacon'], False),
    (['spam', 'bacon', 'eggs'], ['sausage'], False),
))
def test_are_plugins_in_order(available, requested, result):
    assert are_plugins_in_order([{'name': plugin} for plugin in available],
                                *requested) == result


@pytest.mark.parametrize(('test_string', 'labels', 'expected'), [
    ('', {}, ''),
    ('', {'version': 'cat'}, ''),
    ('dog', {'version': 'cat'}, 'dog'),
    ('dog', {}, 'dog'),
    ('{version}', {'version': 'cat'}, 'cat'),
    ('dog-{version}', {'version': 'cat'}, 'dog-cat'),
    ('{version}', {}, None),
    ('{Version}', {'version': 'cat'}, None),
])
def test_label_formatter(labels, test_string, expected):
    if expected is not None:
        assert expected == LabelFormatter().vformat(test_string, [], labels)
    else:
        with pytest.raises(KeyError):
            LabelFormatter().vformat(test_string, [], labels)
