"""
Copyright (c) 2017 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""

from six.moves import configparser
from flexmock import flexmock
import os
import pytest
import re
import shutil
import subprocess
import tarfile
from textwrap import dedent

from modulemd import ModuleMetadata

from atomic_reactor.constants import IMAGE_TYPE_OCI, IMAGE_TYPE_OCI_TAR
from atomic_reactor.inner import DockerBuildWorkflow
from atomic_reactor.plugin import PrePublishPluginsRunner, PluginFailedException
from atomic_reactor.plugins.prepub_flatpak_create_oci import FlatpakCreateOciPlugin
from atomic_reactor.plugins.pre_resolve_module_compose import (ModuleInfo,
                                                               ComposeInfo,
                                                               set_compose_info)
from atomic_reactor.plugins.pre_flatpak_create_dockerfile import (FlatpakSourceInfo,
                                                                  set_flatpak_source_info)
from atomic_reactor.util import ImageName

from tests.constants import TEST_IMAGE
from tests.fixtures import docker_tasker  # noqa
from tests.flatpak import (
    FLATPAK_APP_JSON, FLATPAK_APP_MODULEMD, FLATPAK_APP_RPMS, FLATPAK_APP_FINISH_ARGS,
    FLATPAK_RUNTIME_JSON, FLATPAK_RUNTIME_MODULEMD
)

TEST_ARCH = 'x86_64'

CONTAINER_ID = 'CONTAINER-ID'

ROOT = '/var/tmp/flatpak-build'

DESKTOP_FILE_CONTENTS = """[Desktop Entry]
Name=Image Viewer
Comment=Browse and rotate images
TryExec=eog
Exec=eog %U
Icon=eog
StartupNotify=true
Terminal=false
Type=Application
Categories=GNOME;GTK;Graphics;2DGraphics;RasterGraphics;Viewer;
MimeType=image/bmp;image/gif;image/jpeg;image/jpg;image/pjpeg;image/png;image/tiff;image/x-bmp;image/x-gray;image/x-icb;image/x-ico;image/x-png;image/x-portable-anymap;image/x-portable-bitmap;image/x-portable-graymap;image/x-portable-pixmap;image/x-xbitmap;image/x-xpixmap;image/x-pcx;image/svg+xml;image/svg+xml-compressed;image/vnd.wap.wbmp;
# Extra keywords that can be used to search for eog in GNOME Shell and Unity
Keywords=Picture;Slideshow;Graphics;"""

# The list of RPMs inherited from the runtime is abbreviated; we just need one
# (abattis-cantarell-fonts) to check that they are properly ignored.
APP_MANIFEST_CONTENTS = """eog;3.24.1;1.module_7b96ed10;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
exempi;2.4.2;4.module_7b96ed10;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libexif;0.6.21;11.module_7b96ed10;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libpeas;1.20.0;5.module_7b96ed10;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libpeas-gtk;1.20.0;5.module_7b96ed10;x86_64;0;42;sigmd5;0;42;1491914281;sigpgp;siggpg
abattis-cantarell-fonts;0.0.25;2.module_e15740c0;noarch;0;42;sigmd5;1491914281;sigpgp;siggpg
"""

APP_FILESYSTEM_CONTENTS = {
    '/usr/bin/not_eog': 'SHOULD_IGNORE',
    ROOT + '/usr/bin/also_not_eog': 'SHOULD_IGNORE',
    ROOT + '/app/bin/eog': 'MY_PROGRAM',
    ROOT + '/app/share/applications/eog.desktop': DESKTOP_FILE_CONTENTS,
    ROOT + '/app/share/icons/hicolor/256x256/apps/eog.png': 'MY_ICON',
    '/var/tmp/flatpak-build.rpm_qf': APP_MANIFEST_CONTENTS
}

EXPECTED_APP_FLATPAK_CONTENTS = [
    '/export/share/applications/org.gnome.eog.desktop',
    '/export/share/icons/hicolor/256x256/apps/org.gnome.eog.png',
    '/files/bin/eog',
    '/files/share/applications/org.gnome.eog.desktop',
    '/files/share/icons/hicolor/256x256/apps/eog.png',
    '/files/share/icons/hicolor/256x256/apps/org.gnome.eog.png',
    '/metadata'
]

APP_CONFIG = {
    'base_module': 'eog',
    'modules': {
        'eog': {
            'stream': 'f26',
            'version': '20170629213428',
            'metadata': FLATPAK_APP_MODULEMD,
            'rpms': FLATPAK_APP_RPMS,
        },
        'flatpak-runtime': {
            'stream': 'f26',
            'version': '20170629185228',
            'metadata': FLATPAK_RUNTIME_MODULEMD,
            'rpms': [],  # We don't use this currently
        },
    },
    'flatpak_json': FLATPAK_APP_JSON,
    'filesystem_contents': APP_FILESYSTEM_CONTENTS,
    'expected_contents': EXPECTED_APP_FLATPAK_CONTENTS,
    'expected_components': ['eog'],
    'unexpected_components': ['abattis-cantarell-fonts'],
}

RUNTIME_MANIFEST_CONTENTS = """abattis-cantarell-fonts;0.0.25;2.module_e15740c0;noarch;0;42;sigmd5;1491914281;sigpgp;siggpg
acl;2.2.52;13.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
adwaita-cursor-theme;3.24.0;2.module_e15740c0;noarch;0;42;sigmd5;1491914281;sigpgp;siggpg
adwaita-gtk2-theme;3.22.3;2.module_e15740c0;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
adwaita-icon-theme;3.24.0;2.module_e15740c0;noarch;0;42;sigmd5;1491914281;sigpgp;siggpg
atk;2.24.0;1.module_98c1823a;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
at-spi2-atk;2.24.1;1.module_e15740c0;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
at-spi2-core;2.24.1;1.module_e15740c0;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
audit-libs;2.7.3;1.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
avahi-libs;0.6.32;7.module_f9511cd3;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
basesystem;11;3.module_7e01f122;noarch;0;42;sigmd5;1491914281;sigpgp;siggpg
bash;4.4.11;2.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
bzip2-libs;1.0.6;22.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
ca-certificates;2017.2.11;5.module_7e01f122;noarch;0;42;sigmd5;1491914281;sigpgp;siggpg
cairo;1.14.10;1.module_f9511cd3;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
cairo-gobject;1.14.10;1.module_f9511cd3;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
chkconfig;1.9;1.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
colord-libs;1.3.5;1.module_e15740c0;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
coreutils;8.27;3.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
coreutils-common;8.27;3.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
cracklib;2.9.6;5.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
crypto-policies;20170330;3.git55b66da.module_82827beb;noarch;0;42;sigmd5;1491914281;sigpgp;siggpg
cryptsetup-libs;1.7.3;3.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
cups-libs;2.2.2;6.module_98c1823a;x86_64;1;42;sigmd5;1491914281;sigpgp;siggpg
dbus;1.11.10;2.module_7e01f122;x86_64;1;42;sigmd5;1491914281;sigpgp;siggpg
dbus-libs;1.11.10;2.module_7e01f122;x86_64;1;42;sigmd5;1491914281;sigpgp;siggpg
device-mapper;1.02.137;4.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
device-mapper-libs;1.02.137;4.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
elfutils-default-yama-scope;0.168;5.module_7e01f122;noarch;0;42;sigmd5;1491914281;sigpgp;siggpg
elfutils-libelf;0.168;5.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
elfutils-libs;0.168;5.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
emacs-filesystem;25.2;0.1.rc2.module_7e01f122;noarch;1;42;sigmd5;1491914281;sigpgp;siggpg
enchant;1.6.0;16.module_e15740c0;x86_64;1;42;sigmd5;1491914281;sigpgp;siggpg
expat;2.2.0;2.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
fedora-modular-release;26;4.module_bc43b454;noarch;0;42;sigmd5;1491914281;sigpgp;siggpg
fedora-modular-repos;26;0.1.module_bc43b454;noarch;0;42;sigmd5;1491914281;sigpgp;siggpg
filesystem;3.2;40.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
flatpak-runtime-config;27;3.module_e15740c0;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
fontconfig;2.12.1;4.module_f9511cd3;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
fontpackages-filesystem;1.44;18.module_f9511cd3;noarch;0;42;sigmd5;1491914281;sigpgp;siggpg
freetype;2.7.1;9.module_f9511cd3;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
gawk;4.1.4;3.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
gdbm;1.12;2.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
gdk-pixbuf2;2.36.6;1.module_98c1823a;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
gdk-pixbuf2-modules;2.36.6;1.module_98c1823a;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
glib2;2.52.2;3.module_e15740c0;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
glibc;2.25;4.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
glibc-all-langpacks;2.25;4.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
glibc-common;2.25;4.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
glib-networking;2.50.0;2.module_e15740c0;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
gmp;6.1.2;3.module_7e01f122;x86_64;1;42;sigmd5;1491914281;sigpgp;siggpg
gnome-desktop3;3.24.2;1.module_e15740c0;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
gnome-themes-standard;3.22.3;2.module_e15740c0;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
gnutls;3.5.10;1.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
gobject-introspection;1.52.1;1.module_e15740c0;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
graphite2;1.3.6;2.module_f9511cd3;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
grep;3.0;1.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
gsettings-desktop-schemas;3.24.0;1.module_e15740c0;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
gtk2;2.24.31;3.module_f9511cd3;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
gtk3;3.22.16;1.module_e15740c0;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
gtk-update-icon-cache;3.22.16;1.module_e15740c0;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
gvfs-client;1.32.1;1.module_e15740c0;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
gzip;1.8;2.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
harfbuzz;1.4.4;1.module_f9511cd3;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
hicolor-icon-theme;0.15;4.module_f9511cd3;noarch;0;42;sigmd5;1491914281;sigpgp;siggpg
hunspell;1.5.4;2.module_e15740c0;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
hunspell-en-GB;0.20140811.1;6.module_e15740c0;noarch;0;42;sigmd5;1491914281;sigpgp;siggpg
hunspell-en-US;0.20140811.1;6.module_e15740c0;noarch;0;42;sigmd5;1491914281;sigpgp;siggpg
hwdata;0.301;1.module_f9511cd3;noarch;0;42;sigmd5;1491914281;sigpgp;siggpg
info;6.3;2.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
iptables-libs;1.6.1;2.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
jasper-libs;2.0.12;1.module_f9511cd3;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
jbigkit-libs;2.1;6.module_98c1823a;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
json-glib;1.2.8;1.module_e15740c0;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
keyutils-libs;1.5.9;9.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
kmod-libs;24;1.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
krb5-libs;1.15;9.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
lcms2;2.8;3.module_f9511cd3;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libacl;2.2.52;13.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libappstream-glib;0.7.0;1.module_e15740c0;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libarchive;3.2.2;3.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libattr;2.4.47;18.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libblkid;2.29.1;2.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libcap;2.25;5.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libcap-ng;0.7.8;3.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libcom_err;1.43.4;2.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libcroco;0.6.11;3.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libcrypt;2.25;4.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libdatrie;0.2.9;4.module_f9511cd3;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libdb;5.3.28;17.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libdrm;2.4.81;1.module_98c1823a;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libepoxy;1.4.1;1.module_e15740c0;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libfdisk;2.29.1;2.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libffi;3.1;10.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libgcab1;0.7;2.module_e15740c0;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libgcc;7.0.1;0.15.module_191b5bc9;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libgcrypt;1.7.6;2.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libglvnd;0.2.999;17.20170308git8e6e102.module_f9511cd3;x86_64;1;42;sigmd5;1491914281;sigpgp;siggpg
libglvnd-egl;0.2.999;17.20170308git8e6e102.module_f9511cd3;x86_64;1;42;sigmd5;1491914281;sigpgp;siggpg
libglvnd-glx;0.2.999;17.20170308git8e6e102.module_f9511cd3;x86_64;1;42;sigmd5;1491914281;sigpgp;siggpg
libgpg-error;1.25;2.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libgusb;0.2.10;1.module_e15740c0;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libidn;1.33;2.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libidn2;0.16;2.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libjpeg-turbo;1.5.1;2.module_98c1823a;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libmodman;2.0.1;13.module_f9511cd3;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libmount;2.29.1;2.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libnotify;0.7.7;2.module_e15740c0;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libpcap;1.8.1;3.module_7e01f122;x86_64;14;42;sigmd5;1491914281;sigpgp;siggpg
libpciaccess;0.13.4;4.module_f9511cd3;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libpng;1.6.28;2.module_7e01f122;x86_64;2;42;sigmd5;1491914281;sigpgp;siggpg
libproxy;0.4.15;1.module_f9511cd3;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libpwquality;1.3.0;8.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
librsvg2;2.40.17;1.module_e15740c0;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libseccomp;2.3.2;1.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libselinux;2.6;6.module_98c1823a;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libsemanage;2.6;2.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libsepol;2.6;1.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libsigsegv;2.11;1.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libsmartcols;2.29.1;2.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libsoup;2.58.1;2.module_e15740c0;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libstdc++;7.0.1;0.15.module_191b5bc9;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libstemmer;0;5.585svn.module_f9511cd3;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libtasn1;4.10;2.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libthai;0.1.25;2.module_f9511cd3;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libtiff;4.0.8;1.module_f9511cd3;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libunistring;0.9.7;1.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libusbx;1.0.21;2.module_f9511cd3;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libutempter;1.1.6;9.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libuuid;2.29.1;2.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libverto;0.2.6;7.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libwayland-client;1.13.0;2.module_e15740c0;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libwayland-cursor;1.13.0;2.module_e15740c0;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libwayland-server;1.13.0;2.module_e15740c0;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libX11;1.6.5;2.module_98c1823a;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libX11-common;1.6.5;2.module_98c1823a;noarch;0;42;sigmd5;1491914281;sigpgp;siggpg
libXau;1.0.8;7.module_f9511cd3;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libxcb;1.12;3.module_f9511cd3;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libXcomposite;0.4.4;9.module_98c1823a;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libXcursor;1.1.14;8.module_98c1823a;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libXdamage;1.1.4;9.module_98c1823a;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libXext;1.3.3;5.module_98c1823a;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libXfixes;5.0.3;2.module_98c1823a;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libXft;2.3.2;5.module_98c1823a;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libXi;1.7.9;2.module_98c1823a;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libXinerama;1.1.3;7.module_98c1823a;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libxkbcommon;0.7.1;3.module_e15740c0;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libxml2;2.9.4;2.module_f9511cd3;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libXrandr;1.5.1;2.module_98c1823a;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libXrender;0.9.10;2.module_98c1823a;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libxshmfence;1.2;4.module_98c1823a;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libXtst;1.2.3;2.module_e15740c0;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
libXxf86vm;1.1.4;4.module_f9511cd3;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
lz4-libs;1.7.5;3.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
lzo;2.08;9.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
mesa-libEGL;17.1.4;1.module_e15740c0;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
mesa-libgbm;17.1.4;1.module_e15740c0;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
mesa-libGL;17.1.4;1.module_e15740c0;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
mesa-libglapi;17.1.4;1.module_e15740c0;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
mesa-libwayland-egl;17.1.4;1.module_e15740c0;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
mpfr;3.1.5;3.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
ncurses;6.0;8.20170212.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
ncurses-base;6.0;8.20170212.module_7e01f122;noarch;0;42;sigmd5;1491914281;sigpgp;siggpg
ncurses-libs;6.0;8.20170212.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
nettle;3.3;2.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
openssl-libs;1.1.0e;1.module_7e01f122;x86_64;1;42;sigmd5;1491914281;sigpgp;siggpg
p11-kit;0.23.5;1.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
p11-kit-trust;0.23.5;1.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
pam;1.3.0;2.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
pango;1.40.6;1.module_f9511cd3;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
pcre;8.40;5.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
pixman;0.34.0;3.module_f9511cd3;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
popt;1.16;8.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
python3;3.6.0;21.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
python3-appdirs;1.4.0;10.module_7e01f122;noarch;0;42;sigmd5;1491914281;sigpgp;siggpg
python3-cairo;1.10.0;20.module_e15740c0;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
python3-gobject;3.24.1;1.module_e15740c0;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
python3-gobject-base;3.24.1;1.module_e15740c0;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
python3-libs;3.6.0;21.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
python3-packaging;16.8;4.module_7e01f122;noarch;0;42;sigmd5;1491914281;sigpgp;siggpg
python3-pip;9.0.1;7.module_7e01f122;noarch;0;42;sigmd5;1491914281;sigpgp;siggpg
python3-pyparsing;2.1.10;3.module_7e01f122;noarch;0;42;sigmd5;1491914281;sigpgp;siggpg
python3-setuptools;36.0.1;1.module_e15740c0;noarch;0;42;sigmd5;1491914281;sigpgp;siggpg
python3-six;1.10.0;8.module_e15740c0;noarch;0;42;sigmd5;1491914281;sigpgp;siggpg
qrencode-libs;3.4.2;7.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
readline;7.0;5.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
rest;0.8.0;2.module_e15740c0;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
sed;4.4;1.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
setup;2.10.5;2.module_7e01f122;noarch;0;42;sigmd5;1491914281;sigpgp;siggpg
shadow-utils;4.3.1;3.module_7e01f122;x86_64;2;42;sigmd5;1491914281;sigpgp;siggpg
shared-mime-info;1.8;2.module_f9511cd3;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
sqlite-libs;3.17.0;2.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
systemd;233;3.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
systemd-libs;233;3.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
systemd-pam;233;3.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
system-python;3.6.0;21.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
system-python-libs;3.6.0;21.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
tzdata;2016j;3.module_7e01f122;noarch;0;42;sigmd5;1491914281;sigpgp;siggpg
ustr;1.0.4;22.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
util-linux;2.29.1;2.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
xkeyboard-config;2.21;1.module_e15740c0;noarch;0;42;sigmd5;1491914281;sigpgp;siggpg
xz-libs;5.2.3;2.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
zenity;3.24.0;1.module_e15740c0;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
zlib;1.2.11;2.module_7e01f122;x86_64;0;42;sigmd5;1491914281;sigpgp;siggpg
"""

RUNTIME_FILESYSTEM_CONTENTS = {
    '/usr/bin/not_eog': 'SHOULD_IGNORE',
    ROOT + '/etc/passwd': 'SOME_CONFIG_FILE',
    ROOT + '/usr/bin/bash': 'SOME_BINARY',
    ROOT + '/usr/lib64/libfoo.so.1.0.0': 'SOME_LIB',
    '/var/tmp/flatpak-build.rpm_qf': RUNTIME_MANIFEST_CONTENTS
}

EXPECTED_RUNTIME_FLATPAK_CONTENTS = [
    '/files/bin/bash',
    '/files/etc/passwd',
    '/files/lib64/libfoo.so.1.0.0',
    '/metadata'
]

RUNTIME_CONFIG = {
    'base_module': 'flatpak-runtime',
    'modules': {
        'flatpak-runtime': {
            'stream': 'f26',
            'version': '20170629185228',
            'metadata': FLATPAK_RUNTIME_MODULEMD,
            'rpms': [],  # We don't use this currently
        },
    },
    'flatpak_json': FLATPAK_RUNTIME_JSON,
    'filesystem_contents': RUNTIME_FILESYSTEM_CONTENTS,
    'expected_contents': EXPECTED_RUNTIME_FLATPAK_CONTENTS,
    'expected_components': ['abattis-cantarell-fonts'],
    'unexpected_components': [],
}

CONFIGS = {
    'app': APP_CONFIG,
    'runtime': RUNTIME_CONFIG
}


class MockSource(object):
    dockerfile_path = None
    path = None


class X(object):
    image_id = "xxx"
    source = MockSource()
    base_image = ImageName(repo="qwe", tag="asd")


default_check_output = subprocess.check_output
default_check_call = subprocess.check_call


# Instead of having <repo>/refs/<refname> pointing into <repo>/objects,
# just store the file tree at <repo>/<refname>
class MockOSTree:
    @staticmethod
    def commit(repo, branch, subject, tar_tree, dir_tree):
        branch_path = os.path.join(repo, branch)
        os.makedirs(branch_path)
        with tarfile.open(tar_tree) as tf:
            tf.extractall(path=branch_path)
        for f in os.listdir(dir_tree):
            full = os.path.join(dir_tree, f)
            if os.path.isdir(f):
                shutil.copytree(full, os.path.join(branch_path, f))
            else:
                shutil.copy2(full, os.path.join(branch_path, f))

    @staticmethod
    def init(repo):
        os.mkdir(repo)

    @staticmethod
    def summary(repo):
        pass


# The build directory is created more or less the same as flatpak build-init
# creates it, but when we 'flatpak build-export' we export to the fake
# OSTree format from MockOSTree, and when we 'flatpak build-bundle', we
# create a fake 'OCI Image' where we just have <dir>/tree with the filesystem
# contents, instead of having an index.json, tarred layers, etc.
class MockFlatpak:
    @staticmethod
    def default_arch():
        return TEST_ARCH

    @staticmethod
    def build_bundle(repo, filename, name, branch='master', runtime=False):
        if runtime:
            ref = 'runtime/' + name
        else:
            ref = 'app/' + name

        if branch is None:
            branch = os.listdir(os.path.join(repo, ref))[0]
        branch_path = os.path.join(repo, ref, TEST_ARCH, branch)
        dest_path = os.path.join(filename, 'tree')
        os.makedirs(filename)
        shutil.copytree(branch_path, dest_path)

    @staticmethod
    def build_init(directory, appname, sdk, runtime, runtime_branch):
        if not os.path.isdir(directory):
            os.mkdir(directory)
        with open(os.path.join(directory, "metadata"), "w") as f:
            f.write(dedent("""\
                           [Application]
                           name={appname}
                           runtime={runtime}/{arch}/{runtime_branch}
                           sdk={sdk}/{arch}/{runtime_branch}
                           """.format(appname=appname,
                                      sdk=sdk,
                                      runtime=runtime,
                                      runtime_branch=runtime_branch,
                                      arch=TEST_ARCH)))
        os.mkdir(os.path.join(directory, "files"))

    @staticmethod
    def build_finish(directory):
        pass

    @staticmethod
    def build_export(repo, directory):
        cp = configparser.RawConfigParser()
        cp.read(os.path.join(directory, "metadata"))
        appname = cp.get('Application', 'name')
        ref = os.path.join('app', appname, TEST_ARCH, 'master')

        dest = os.path.join(repo, ref)
        filesdir = os.path.join(directory, "files")
        shutil.copytree(filesdir, os.path.join(dest, "files"))
        shutil.copy2(os.path.join(directory, "metadata"), dest)

        # Simplified implementation of exporting files into /export
        # flatpak build-export only actually handles very specific files
        # desktop files in share/applications, icons, etc.
        dest_exportdir = os.path.join(dest, "export")
        for dirpath, dirname, filenames in os.walk(filesdir):
            rel_dirpath = os.path.relpath(dirpath, filesdir)
            for f in filenames:
                if f.startswith(appname):
                    destdir = os.path.join(dest_exportdir, rel_dirpath)
                    os.makedirs(destdir)
                    shutil.copy2(os.path.join(dirpath, f), destdir)


COMMAND_PATTERNS = [
    (['flatpak', '--default-arch'], MockFlatpak.default_arch),
    (['flatpak', 'build-bundle', '@repo',
      '--oci', '--runtime', '@filename', '@name', '@branch'],
     MockFlatpak.build_bundle, {'runtime': True}),
    (['flatpak', 'build-bundle', '@repo',
      '--oci', '@filename', '@name'],
     MockFlatpak.build_bundle),
    (['flatpak', 'build-export', '@repo', '@directory'],
     MockFlatpak.build_export),
    (['flatpak', 'build-finish'] + FLATPAK_APP_FINISH_ARGS + ['@directory'],
     MockFlatpak.build_finish),
    (['flatpak', 'build-init', '@directory', '@appname', '@sdk', '@runtime', '@runtime_branch'],
     MockFlatpak.build_init),
    (['ostree', 'commit',
      '--repo', '@repo',
      '--owner-uid=0', '--owner-gid=0', '--no-xattrs',
      '--branch', '@branch', '-s', '@subject', '--tree=tar=@tar_tree', '--tree=dir=@dir_tree'],
     MockOSTree.commit),
    (['ostree', 'init', '--mode=archive-z2', '--repo', '@repo'], MockOSTree.init),
    (['ostree', 'summary', '-u', '--repo', '@repo'], MockOSTree.summary)
]


def mock_command(cmdline, return_output=False, universal_newlines=False, cwd=None):
    output = ''
    cmd = cmdline[0]

    if cmd not in ('flatpak', 'ostree'):
        if output:
            return default_check_output(cmdline, universal_newlines=universal_newlines, cwd=cwd)
        else:
            return default_check_call(cmdline, cwd=cwd)

    for command in COMMAND_PATTERNS:
        if len(command) == 2:
            pattern, f = command
            default_args = {}
        else:
            pattern, f, default_args = command

        if len(pattern) != len(cmdline):
            continue

        matched = True
        kwargs = None
        for i, pattern_arg in enumerate(pattern):
            arg = cmdline[i]
            at_index = pattern_arg.find("@")
            if at_index < 0:
                if pattern_arg != arg:
                    matched = False
                    break
            else:
                before = pattern_arg[0:at_index]
                if not arg.startswith(before):
                    matched = False
                    break
                if kwargs is None:
                    kwargs = dict(default_args)
                kwargs[pattern_arg[at_index + 1:]] = arg[len(before):]

        if not matched:
            continue

        if kwargs is None:
            kwargs = dict(default_args)

        output = f(**kwargs)
        if output is None:
            output = ''

        if return_output:
            if universal_newlines:
                return output
            else:
                return output.encode('UTF-8')

    raise RuntimeError("Unmatched command line to mock %r" % cmdline)


def mocked_check_call(cmdline, cwd=None):
    mock_command(cmdline, return_output=True, cwd=cwd)


def mocked_check_output(cmdline, universal_newlines=False, cwd=None):
    return mock_command(cmdline, return_output=True, universal_newlines=universal_newlines, cwd=cwd)


class DefaultInspector(object):
    def __init__(self, tmpdir, metadata):
        # Import the OCI bundle into a ostree repository for examination
        self.repodir = os.path.join(str(tmpdir), 'repo')
        default_check_call(['ostree', 'init', '--mode=archive-z2', '--repo=' + self.repodir])
        default_check_call(['flatpak', 'build-import-bundle', '--oci',
                            self.repodir, str(metadata['path'])])

        self.ref_name = metadata['ref_name']

    def list_files(self):
        output = default_check_output(['ostree', '--repo=' + self.repodir,
                                       'ls', '-R', self.ref_name],
                                      universal_newlines=True)
        files = []
        for line in output.split('\n'):
            line = line.strip()
            if line == '':
                continue
            perms, user, group, size, path = line.split()
            if perms.startswith('d'):  # A directory
                continue
            files.append(path)

        return files

    def cat_file(self, path):
        return default_check_output(['ostree', '--repo=' + self.repodir,
                                     'cat', self.ref_name,
                                     path],
                                    universal_newlines=True)


class MockInspector(object):
    def __init__(self,  tmpdir, metadata):
        self.path = metadata['path']

    def list_files(self):
        def _make_absolute(path):
            if path.startswith("./"):
                return path[1:]
            else:
                return '/' + path

        files = []
        top = os.path.join(self.path, 'tree')
        for dirpath, dirname, filenames in os.walk(top):
            rel_dirpath = os.path.relpath(dirpath, top)
            files.extend([_make_absolute(os.path.join(rel_dirpath, f)) for f in filenames])

        return files

    def cat_file(self, path):
        full = os.path.join(self.path, 'tree', path[1:])
        with open(full, "r") as f:
            return f.read()


@pytest.mark.parametrize('config_name, stray_component, missing_component', [ # noqa - docker_tasker fixture
    ('app', False, False),
    ('app', True, False),
    ('runtime', False, False),
    ('runtime', True, False),
    ('runtime', False, True),
])
@pytest.mark.parametrize('mock_flatpak', (False, True))
def test_flatpak_create_oci(tmpdir, docker_tasker, config_name,
                            stray_component, missing_component, mock_flatpak):
    if not mock_flatpak:
        # Check that we actually have flatpak available
        have_flatpak = False
        try:
            output = subprocess.check_output(['flatpak', '--version'],
                                             universal_newlines=True)
            m = re.search('(\d+)\.(\d+)\.(\d+)', output)
            if m and (int(m.group(1)), int(m.group(2)), int(m.group(3))) >= (0, 9, 7):
                have_flatpak = True

        except (subprocess.CalledProcessError, OSError):
            pass

        if not have_flatpak:
            return

    config = CONFIGS[config_name]

    if mock_flatpak:
        (flexmock(subprocess)
         .should_receive("check_call")
         .replace_with(mocked_check_call))

        (flexmock(subprocess)
         .should_receive("check_output")
         .replace_with(mocked_check_output))

    workflow = DockerBuildWorkflow({"provider": "git", "uri": "asd"}, TEST_IMAGE)
    setattr(workflow, 'builder', X)
    setattr(workflow.builder, 'tasker', docker_tasker)

    filesystem_dir = os.path.join(str(tmpdir), 'filesystem')
    os.mkdir(filesystem_dir)

    filesystem_contents = config['filesystem_contents']

    for path, contents in filesystem_contents.items():
        fullpath = os.path.join(filesystem_dir, path[1:])
        parent_dir = os.path.dirname(fullpath)
        if not os.path.isdir(parent_dir):
            os.makedirs(parent_dir)

        with open(fullpath, 'w') as f:
            f.write(contents)

    if stray_component:
        fullpath = os.path.join(filesystem_dir, 'var/tmp/flatpak-build.rpm_qf')
        with open(fullpath, 'a') as f:
            f.write("bad-rpm;1.2.3;1.fc26;x86_64;0;42;sigmd5;0;42;1491914281;sigpgp;siggpg\n")

    if missing_component:
        fullpath = os.path.join(filesystem_dir, 'var/tmp/flatpak-build.rpm_qf')
        with open(fullpath, 'r') as f:
            with open(fullpath + '.tmp', 'w') as g:
                f.readline()
                g.write(f.read())
        os.rename(fullpath + '.tmp', fullpath)

    filesystem_tar = os.path.join(filesystem_dir, 'tar')
    with open(filesystem_tar, "wb") as f:
        with tarfile.TarFile(fileobj=f, mode='w') as tf:
            for f in os.listdir(filesystem_dir):
                tf.add(os.path.join(filesystem_dir, f), f)

    export_stream = open(filesystem_tar, "rb")

    (flexmock(docker_tasker.d.wrapped)
     .should_receive('create_container')
     .with_args(workflow.image)
     .and_return({'Id': CONTAINER_ID}))
    (flexmock(docker_tasker.d.wrapped)
     .should_receive('export')
     .with_args(CONTAINER_ID)
     .and_return(export_stream))
    (flexmock(docker_tasker.d.wrapped)
     .should_receive('remove_container')
     .with_args(CONTAINER_ID))

    modules = {}
    for name, module_config in config['modules'].items():
        mmd = ModuleMetadata()
        mmd.loads(module_config['metadata'])
        modules[name] = ModuleInfo(name,
                                   module_config['stream'],
                                   module_config['version'],
                                   mmd,
                                   module_config['rpms'])
    base_module = modules[config['base_module']]

    repo_url = 'http://odcs.example/composes/latest-odcs-42-1/compose/Temporary/$basearch/os/'
    compose_info = ComposeInfo(42, base_module,
                               modules,
                               repo_url)
    set_compose_info(workflow, compose_info)

    source = FlatpakSourceInfo(FLATPAK_APP_JSON,
                               compose_info)
    set_flatpak_source_info(workflow, source)

    runner = PrePublishPluginsRunner(
        docker_tasker,
        workflow,
        [{
            'name': FlatpakCreateOciPlugin.key,
            'args': {}
        }]
    )

    if stray_component:
        with pytest.raises(PluginFailedException) as ex:
            runner.run()
        assert 'bad-rpm' in str(ex)
    elif missing_component:
        with pytest.raises(PluginFailedException) as ex:
            runner.run()
        assert 'does not match runtime profile' in str(ex)
    else:
        runner.run()

        dir_metadata = workflow.exported_image_sequence[-2]
        assert dir_metadata['type'] == IMAGE_TYPE_OCI

        tar_metadata = workflow.exported_image_sequence[-1]
        assert tar_metadata['type'] == IMAGE_TYPE_OCI_TAR

        # Check that the expected files ended up in the flatpak

        if mock_flatpak:
            inspector = MockInspector(tmpdir, dir_metadata)
        else:
            inspector = DefaultInspector(tmpdir, dir_metadata)

        files = inspector.list_files()
        assert sorted(files) == config['expected_contents']

        components = {c['name'] for c in workflow.image_components}
        for n in config['expected_components']:
            assert n in components
        for n in config['unexpected_components']:
            assert n not in components

        if config_name is 'app':
            # Check that the desktop file was rewritten
            output = inspector.cat_file('/export/share/applications/org.gnome.eog.desktop')
            lines = output.split('\n')
            assert 'Icon=org.gnome.eog' in lines
