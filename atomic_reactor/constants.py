"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""

import os

DOCKER_SOCKET_PATH = '/var/run/docker.sock'
DOCKERFILE_FILENAME = 'Dockerfile'
BUILD_JSON = 'build.json'
BUILD_JSON_ENV = 'BUILD_JSON'
RESULTS_JSON = 'results.json'

CONTAINER_SHARE_PATH = '/run/share/'
CONTAINER_SHARE_SOURCE_SUBDIR = 'source'
CONTAINER_SECRET_PATH = ''
CONTAINER_BUILD_JSON_PATH = os.path.join(CONTAINER_SHARE_PATH, BUILD_JSON)
CONTAINER_RESULTS_JSON_PATH = os.path.join(CONTAINER_SHARE_PATH, RESULTS_JSON)
CONTAINER_DOCKERFILE_PATH = os.path.join(CONTAINER_SHARE_PATH, DOCKERFILE_FILENAME)

HOST_SECRET_PATH = ''

EXPORTED_SQUASHED_IMAGE_NAME = 'image.tar'
EXPORTED_COMPRESSED_IMAGE_NAME_TEMPLATE = 'compressed.tar.{0}'

YUM_REPOS_DIR = '/etc/yum.repos.d/'
RELATIVE_REPOS_PATH = "atomic-reactor-repos/"
DEFAULT_YUM_REPOFILE_NAME = 'atomic-reactor-injected.repo'

# key in dictionary returned by "docker inspect" that holds the image
# configuration (such as labels)
INSPECT_CONFIG = "Config"

# docs constants

DESCRIPTION = "Python library with command line interface for building docker images."
HOMEPAGE = "https://github.com/projectatomic/atomic-reactor"
PROG = "atomic-reactor"
MANPAGE_AUTHORS = "Jiri Popelka <jpopelka@redhat.com>, " \
                  "Martin Milata <mmilata@redhat.com>, " \
                  "Slavek Kabrda <slavek@redhat.com>, " \
                  "Tim Waugh <twaugh@redhat.com>, " \
                  "Tomas Tomecek <ttomecek@redhat.com>"
MANPAGE_SECTION = 1


# debug print of tools reactor uses

TOOLS_USED = (
    {"pkg_name": "docker", "display_name": "docker-py"},
    {"pkg_name": "docker_squash"},
    {"pkg_name": "atomic_reactor"},
    {"pkg_name": "osbs", "display_name": "osbs-client"},
    {"pkg_name": "dockpulp"},
)

DEFAULT_DOWNLOAD_BLOCK_SIZE = 10 * 1024 * 1024  # 10Mb

TAG_NAME_REGEX = r'^[\w][\w.-]{0,127}$'

PLUGIN_KOJI_PROMOTE_PLUGIN_KEY = 'koji_promote'
PLUGIN_KOJI_IMPORT_PLUGIN_KEY = 'koji_import'
PLUGIN_KOJI_UPLOAD_PLUGIN_KEY = 'koji_upload'
PLUGIN_KOJI_TAG_BUILD_KEY = 'koji_tag_build'
PLUGIN_PULP_PUSH_KEY = 'pulp_push'
PLUGIN_PULP_SYNC_KEY = 'pulp_sync'
PLUGIN_PULP_TAG_KEY = 'pulp_tag'
PLUGIN_ADD_FILESYSTEM_KEY = 'add_filesystem'
PLUGIN_FETCH_WORKER_METADATA_KEY = 'fetch_worker_metadata'
