"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""

from __future__ import unicode_literals

import os
import docker
from flexmock import flexmock
import requests

from atomic_reactor.constants import DOCKER_SOCKET_PATH
from atomic_reactor.util import ImageName
from tests.constants import COMMAND, IMPORTED_IMAGE_ID

old_ope = os.path.exists

mock_containers = \
    [{'Created': 1430292310,
      'Image': 'fedora',
      'Names': ['/goofy_mayer'],
      'Command': '/bin/bash',
      'Id': 'f8ee920b2db5e802da2583a13a4edbf0523ca5fff6b6d6454c1fd6db5f38014d',
      'Status': 'Up 2 seconds'},
     {'Created': 1430293290,
      'Image': 'busybox:latest',
      'Names': ['/boring_mestorf'],
      'Id': '105026325ff668ccf4dc2bcf4f009ea35f2c6a933a778993e6fad3c50173aaab',
      'Command': COMMAND}]

mock_image = \
    {'Created': 1414577076,
     'Id': '3ab9a7ed8a169ab89b09fb3e12a14a390d3c662703b65b4541c0c7bde0ee97eb',
     'ParentId': 'a79ad4dac406fcf85b9c7315fe08de5b620c1f7a12f45c8185c843f4b4a49c4e',
     'RepoTags': ['buildroot-fedora:latest'],
     'Size': 0,
     'VirtualSize': 856564160}

mock_images = None

mock_logs = b'uid=0(root) gid=0(root) groups=10(wheel)'

mock_build_logs = \
    [b'{"stream":"Step 0 : FROM fedora:latest\\n"}\r\n',
     b'{"status":"Pulling from fedora","id":"latest"}\r\n',
     b'{"status":"Digest: sha256:c63476a082b960f6264e59ef0ff93a9169eac8daf59e24805e0382afdcc9082f"}\r\n',  # noqa
     b'{"status":"Status: Image is up to date for fedora:latest"}\r\n',
     b'{"stream":"Step 1 : RUN uname -a \\u0026\\u0026 env\\n"}\r\n',
     b'{"stream":" ---\\u003e Running in 3600c91d1c40\\n"}\r\n',
     b'{"stream":"Removing intermediate container 3600c91d1c40\\n"}\r\n',
     b'{"stream":"Successfully built 1793c2380436\\n"}\r\n']

mock_build_logs_failed = mock_build_logs + \
    [b'{"errorDetail":{"code":2,"message":"The command \\u0026{[/bin/sh -c ls -lha /a/b/c]} returned a non-zero code: 2"},\
        "error":"The command \\u0026{[/bin/sh -c ls -lha /a/b/c]} returned a non-zero code: 2"}\r\n']  # noqa

mock_pull_logs = \
    [b'{"stream":"Trying to pull repository localhost:5000/busybox ..."}\r\n',
     b'{"status":"Pulling image (latest) from localhost:5000/busybox","progressDetail":{},"id":"8c2e06607696"}',  # noqa
     b'{"status":"Download complete","progressDetail":{},"id":"8c2e06607696"}',
     b'{"status":"Status: Image is up to date for localhost:5000/busybox:latest"}\r\n']

mock_pull_logs_failed = \
    [b'{"errorDetail":{"message":"Error: image ***:latest not found"},"error":"Error: image ***:latest not found"}']  # noqa

mock_push_logs = \
    [b'{"status":"The push refers to a repository [localhost:5000/busybox] (len: 1)"}\r\n',
     b'{"status":"Image already exists","progressDetail":{},"id":"17583c7dd0da"}\r\n',
     b'{"status":"Image already exists","progressDetail":{},"id":"d1592a710ac3"}\r\n'
     b'{"status":"latest: digest: sha256:afe8a267153784d570bfea7d22699c612a61f984e2b9a93135660bb85a3113cf size: 2735"}\r\n']  # noqa

mock_push_logs_failed = \
    [b'{"status":"The push refers to a repository [localhost:5000/busybox] (len: 1)"}\r\n',
     b'{"status":"Sending image list"}\r\n',
     b'{"errorDetail":{"message":"Put http://localhost:5000/v1/repositories/busybox/: dial tcp [::1]:5000: getsockopt: connection refused"},"error":"Put http://localhost:5000/v1/repositories/busybox/: dial tcp [::1]:5000: getsockopt: connection refused"}\r\n']  # noqa

mock_info = {
    'BridgeNfIp6tables': True,
    'BridgeNfIptables': True,
    'Containers': 18,
    'CpuCfsPeriod': True,
    'CpuCfsQuota': True,
    'Debug': False,
    'DockerRootDir': '/var/lib/docker',
    'Driver': 'overlay',
    'DriverStatus': [['Backing Filesystem', 'xfs']],
    'ExecutionDriver': 'native-0.2',
    'ExperimentalBuild': False,
    'HttpProxy': '',
    'HttpsProxy': '',
    'ID': 'YC7N:MYIE:6SEL:JYLU:SRIG:PCVV:APZD:WTH4:4MGR:N4BG:CT53:ZW2O',
    'IPv4Forwarding': True,
    'Images': 162,
    'IndexServerAddress': 'https://index.docker.io/v1/',
    'InitPath': '/usr/libexec/docker/dockerinit',
    'InitSha1': 'eb5677df79a87639f30ab5c2c01e5170abc96af2',
    'KernelVersion': '4.1.4-200.fc22.x86_64',
    'Labels': None,
    'LoggingDriver': 'json-file',
    'MemTotal': 12285665280,
    'MemoryLimit': True,
    'NCPU': 4,
    'NEventsListener': 0,
    'NFd': 15,
    'NGoroutines': 31,
    'Name': 'the-build-host',
    'NoProxy': '',
    'OomKillDisable': True,
    'OperatingSystem': 'Fedora 24 (Rawhide) (containerized)',
    'RegistryConfig': {'IndexConfigs': {'127.0.0.1:5000': {'Mirrors': [],
                                                           'Name': '127.0.0.1:5000',
                                                           'Official': False,
                                                           'Secure': False},
                                        '172.17.0.1:5000': {'Mirrors': [],
                                                            'Name': '172.17.0.1:5000',
                                                            'Official': False,
                                                            'Secure': False},
                                        '172.17.0.2:5000': {'Mirrors': [],
                                                            'Name': '172.17.0.2:5000',
                                                            'Official': False,
                                                            'Secure': False},
                                        '172.17.0.3:5000': {'Mirrors': [],
                                                            'Name': '172.17.0.3:5000',
                                                            'Official': False,
                                                            'Secure': False},
                                        'docker.io': {'Mirrors': None,
                                                      'Name': 'docker.io',
                                                      'Official': True,
                                                      'Secure': True}
                                        },
                       'InsecureRegistryCIDRs': ['127.0.0.0/8'], 'Mirrors': None},
    'SwapLimit': True,
    'SystemTime': '2015-09-15T16:38:50.585211559+02:00'
}

mock_version = {
    'ApiVersion': '1.21',
    'Arch': 'amd64',
    'BuildTime': 'Thu Sep 10 17:53:19 UTC 2015',
    'GitCommit': 'af9b534-dirty',
    'GoVersion': 'go1.5.1',
    'KernelVersion': '4.1.4-200.fc22.x86_64',
    'Os': 'linux',
    'Version': '1.9.0-dev-fc24'
}

mock_import_image = '{"status": "%s"}' % IMPORTED_IMAGE_ID

mock_inspect_container = {
    'Id': 'f8ee920b2db5e802da2583a13a4edbf0523ca5fff6b6d6454c1fd6db5f38014d',
    'Mounts': [
        {
            "Source": "/mnt/tmp",
            "Destination": "/tmp",
            "Mode": "",
            "RW": True,
            "Propagation": "rprivate",
            "Name": "test"
        },
        {
            "Source": "/mnt/conflict_exception",
            "Destination": "/exception",
            "Mode": "",
            "RW": True,
            "Propagation": "rprivate",
            "Name": "conflict_exception"
        },
        {
            "Source": "/mnt/real_exception",
            "Destination": "/exception",
            "Mode": "",
            "RW": True,
            "Propagation": "rprivate",
            "Name": "real_exception"
        },
        {
            "Source": "",
            "Destination": "/skip_me",
            "Mode": "",
            "RW": True,
            "Propagation": "rprivate",
            "Name": "skip_me"
        }
    ]
}


def _find_image(img, ignore_registry=False):
    global mock_images

    for im in mock_images:
        im_name = im['RepoTags'][0]
        if im_name == img:
            return im
        if ignore_registry:
            im_name_wo_reg = ImageName.parse(im_name).to_str(registry=False)
            if im_name_wo_reg == img:
                return im

    return None


def _docker_exception(code=404, content='not found'):
    response = flexmock(content=content, status_code=code)
    return docker.errors.APIError(code, response)


def _mock_pull(repo, tag='latest', **kwargs):
    im = ImageName.parse(repo)
    if im.repo == 'library-only' and im.namespace != 'library':
        return iter(mock_pull_logs_failed)

    repotag = '%s:%s' % (repo, tag)
    if _find_image(repotag) is None:
        new_image = mock_image.copy()
        new_image['RepoTags'] = [repotag]
        mock_images.append(new_image)

    return iter(mock_pull_logs)


def _mock_remove_image(img, **kwargs):
    i = _find_image(img)
    if i is not None:
        mock_images.remove(i)
        return None

    raise _docker_exception()


def _mock_inspect(img, **kwargs):
    # real 'docker inspect busybox' returns info even there's only localhost:5000/busybox
    i = _find_image(img, ignore_registry=True)
    if i is not None:
        return i

    raise _docker_exception()


def _mock_tag(src_img, dest_repo, dest_tag='latest', **kwargs):
    i = _find_image(src_img)
    if i is None:
        raise _docker_exception()

    dst_img = "%s:%s" % (dest_repo, dest_tag)
    i = _find_image(dst_img)
    if i is None:
        new_image = mock_image.copy()
        new_image['RepoTags'] = [dst_img]
        mock_images.append(new_image)

    return True


def _mock_generator_raises():
    raise RuntimeError("build generator failure")
    yield {}


def mock_docker(build_should_fail=False,
                inspect_should_fail=False,
                wait_should_fail=False,
                provided_image_repotags=None,
                should_raise_error={},
                remember_images=False,
                push_should_fail=False,
                build_should_fail_generator=False):
    """
    mock all used docker.APIClient methods

    :param build_should_fail: True == build() log will contain error
    :param inspect_should_fail: True == inspect_image() will return None
    :param wait_should_fail: True == wait() will return 1 instead of 0
    :param provided_image_repotags: images() will contain provided image
    :param should_raise_error: methods (with args) to raise docker.errors.APIError
    :param remember_images: keep track of available image tags
    """
    if provided_image_repotags:
        mock_image['RepoTags'] = provided_image_repotags
    inspect_image_result = None if inspect_should_fail else mock_image
    push_result = mock_push_logs if not push_should_fail else mock_push_logs_failed

    if build_should_fail:
        if build_should_fail_generator:
            build_result = _mock_generator_raises()
        else:
            build_result = iter(mock_build_logs_failed)
    else:
        build_result = iter(mock_build_logs)

    if not hasattr(docker, 'APIClient'):
        setattr(docker, 'APIClient', docker.Client)

    flexmock(docker.APIClient, build=lambda **kwargs: build_result)
    flexmock(docker.APIClient, commit=lambda cid, **kwargs: mock_containers[0])
    flexmock(docker.APIClient, containers=lambda **kwargs: mock_containers)
    flexmock(docker.APIClient, create_container=lambda img, **kwargs: mock_containers[0])
    flexmock(docker.APIClient, images=lambda **kwargs: [mock_image])
    flexmock(docker.APIClient, inspect_image=lambda im_id: inspect_image_result)
    flexmock(docker.APIClient, inspect_container=lambda im_id: mock_inspect_container)
    flexmock(docker.APIClient, logs=lambda cid, **kwargs: iter([mock_logs]) if kwargs.get('stream')
             else mock_logs)
    flexmock(docker.APIClient, pull=lambda img, **kwargs: iter(mock_pull_logs))
    flexmock(docker.APIClient, push=lambda iid, **kwargs: iter(push_result))
    flexmock(docker.APIClient, remove_container=lambda cid, **kwargs: None)
    flexmock(docker.APIClient, remove_image=lambda iid, **kwargs: None)
    flexmock(docker.APIClient, start=lambda cid, **kwargs: None)
    flexmock(docker.APIClient, tag=lambda img, rep, **kwargs: True)
    flexmock(docker.APIClient, wait=lambda cid: 1 if wait_should_fail else 0)
    flexmock(docker.APIClient, version=lambda **kwargs: mock_version)
    flexmock(docker.APIClient, info=lambda **kwargs: mock_info)
    flexmock(docker.APIClient, import_image_from_data=lambda url: mock_import_image)
    flexmock(docker.APIClient, import_image_from_stream=lambda url: mock_import_image)

    class GetImageResult(object):
        data = b''

        def __init__(self):
            self.fp = open(__file__, 'rb')

        def __getattr__(self, attr):
            return getattr(self, self.fp, attr)

        def __enter__(self):
            return self.fp

        def __exit__(self, tp, val, tb):
            self.fp.close()

    flexmock(docker.APIClient, get_image=lambda img, **kwargs: GetImageResult())
    flexmock(os.path, exists=lambda p: True if p == DOCKER_SOCKET_PATH else old_ope(p))

    def remove_volume(volume_name):
        if 'exception' in volume_name:
            if volume_name == 'conflict_exception':
                response = flexmock(content="abc", status_code=requests.codes.CONFLICT)
            else:
                response = flexmock(content="abc", status_code=requests.codes.NOT_FOUND)
            raise docker.errors.APIError("failed to remove volume %s" % volume_name, response)
        return None

    flexmock(docker.APIClient, remove_volume=lambda iid, **kwargs: remove_volume(iid))

    for method, args in should_raise_error.items():
        response = flexmock(content="abc", status_code=123)
        if args:
            (flexmock(docker.APIClient)
             .should_receive(method)
             .with_args(*args).and_raise(docker.errors.APIError, "xyz",
                                         response))
        else:
            (flexmock(docker.APIClient)
             .should_receive(method)
             .and_raise(docker.errors.APIError, "xyz", response))

    if remember_images:
        global mock_images
        mock_images = [mock_image]

        flexmock(docker.APIClient, inspect_image=_mock_inspect)
        flexmock(docker.APIClient, pull=_mock_pull)
        flexmock(docker.APIClient, remove_image=_mock_remove_image)
        flexmock(docker.APIClient, tag=_mock_tag)

    flexmock(docker.APIClient, _retrieve_server_version=lambda: '1.20')
