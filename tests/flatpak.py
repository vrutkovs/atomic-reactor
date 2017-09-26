FLATPAK_APP_MODULEMD = """
document: modulemd
version: 1
data:
  name: eog
  stream: f26
  version: 20170629213428
  summary: Eye of GNOME Application Module
  description: The Eye of GNOME image viewer (eog) is the official image viewer for
    the GNOME desktop. It can view single image files in a variety of formats, as
    well as large image collections.
  license:
    module: [MIT]
  dependencies:
    buildrequires: {base-runtime: f26, common-build-dependencies: f26, flatpak-runtime: f26,
      perl: f26, shared-userspace: f26}
    requires: {flatpak-runtime: f26}
  profiles:
    default:
      rpms: [eog]
  components:
    rpms: {}
  xmd:
    mbs: OMITTED
"""

FLATPAK_APP_RPMS = [
    "eog-0:3.24.1-1.module_7b96ed10.src.rpm",
    "eog-0:3.24.1-1.module_7b96ed10.x86_64.rpm",
    "eog-debuginfo-0:3.24.1-1.module_7b96ed10.x86_64.rpm",
    "eog-devel-0:3.24.1-1.module_7b96ed10.x86_64.rpm",
    "eog-tests-0:3.24.1-1.module_7b96ed10.x86_64.rpm",
    "exempi-0:2.4.2-4.module_7b96ed10.src.rpm",
    "exempi-0:2.4.2-4.module_7b96ed10.x86_64.rpm",
    "exempi-debuginfo-0:2.4.2-4.module_7b96ed10.x86_64.rpm",
    "exempi-devel-0:2.4.2-4.module_7b96ed10.x86_64.rpm",
    "glade-0:3.20.0-3.module_7b96ed10.src.rpm",
    "glade-0:3.20.0-3.module_7b96ed10.x86_64.rpm",
    "glade-debuginfo-0:3.20.0-3.module_7b96ed10.x86_64.rpm",
    "glade-devel-0:3.20.0-3.module_7b96ed10.x86_64.rpm",
    "glade-libs-0:3.20.0-3.module_7b96ed10.x86_64.rpm",
    "libexif-0:0.6.21-11.module_7b96ed10.src.rpm",
    "libexif-0:0.6.21-11.module_7b96ed10.x86_64.rpm",
    "libexif-debuginfo-0:0.6.21-11.module_7b96ed10.x86_64.rpm",
    "libexif-devel-0:0.6.21-11.module_7b96ed10.x86_64.rpm",
    "libexif-doc-0:0.6.21-11.module_7b96ed10.x86_64.rpm",
    "libpeas-0:1.20.0-5.module_7b96ed10.src.rpm",
    "libpeas-0:1.20.0-5.module_7b96ed10.x86_64.rpm",
    "libpeas-debuginfo-0:1.20.0-5.module_7b96ed10.x86_64.rpm",
    "libpeas-devel-0:1.20.0-5.module_7b96ed10.x86_64.rpm",
    "libpeas-gtk-0:1.20.0-5.module_7b96ed10.x86_64.rpm",
    "libpeas-loader-python-0:1.20.0-5.module_7b96ed10.x86_64.rpm",
    "libpeas-loader-python3-0:1.20.0-5.module_7b96ed10.x86_64.rpm",
]

FLATPAK_APP_FINISH_ARGS = [
    "--filesystem=host",
    "--share=ipc",
    "--socket=x11",
    "--socket=wayland",
    "--socket=session-bus",
    "--filesystem=~/.config/dconf:ro",
    "--filesystem=xdg-run/dconf",
    "--talk-name=ca.desrt.dconf",
    "--env=DCONF_USER_CONFIG_DIR=.config/dconf"
]

FLATPAK_APP_JSON = {
    "id": "org.gnome.eog",
    "version": "3.20.0-2.fc26",
    "runtime": "org.fedoraproject.Platform",
    "runtime-version": "26",
    "sdk": "org.fedoraproject.Sdk",
    "command": "eog",
    "tags": ["Viewer"],
    "finish-args": FLATPAK_APP_FINISH_ARGS
}

FLATPAK_RUNTIME_MODULEMD = """
document: modulemd
version: 1
data:
  name: flatpak-runtime
  stream: f26
  version: 20170701152209
  summary: Flatpak Runtime
  description: Libraries and data files shared between applications
  api:
    rpms: [librsvg2, gnome-themes-standard, abattis-cantarell-fonts, rest, xkeyboard-config,
      adwaita-cursor-theme, python3-gobject-base, json-glib, zenity, gsettings-desktop-schemas,
      glib-networking, gobject-introspection, gobject-introspection-devel, flatpak-rpm-macros,
      python3-gobject, gvfs-client, colord-libs, flatpak-runtime-config, hunspell-en-GB,
      libsoup, glib2-devel, hunspell-en-US, at-spi2-core, gtk3, libXtst, adwaita-gtk2-theme,
      libnotify, adwaita-icon-theme, libgcab1, libxkbcommon, libappstream-glib, python3-cairo,
      gnome-desktop3, libepoxy, hunspell, libgusb, glib2, enchant, at-spi2-atk]
  dependencies:
    buildrequires: {bootstrap: f26, shared-userspace: f26}
    requires: {base-runtime: f26, shared-userspace: f26}
  license:
    module: [MIT]
  profiles:
    buildroot:
      rpms: [flatpak-rpm-macros, flatpak-runtime-config]
    runtime:
      rpms: [libwayland-server, librsvg2, libX11, libfdisk, adwaita-cursor-theme,
        libsmartcols, popt, gdbm, libglvnd, openssl-libs, gobject-introspection, systemd,
        ncurses-base, lcms2, libpcap, crypto-policies, fontconfig, libacl, libwayland-cursor,
        libseccomp, gmp, jbigkit-libs, bzip2-libs, libunistring, freetype, nettle,
        libidn, python3-six, gtk2, gtk3, ca-certificates, libdrm, rest, lzo, libcap,
        gnutls, pango, util-linux, basesystem, p11-kit, libgcab1, iptables-libs, dbus,
        python3-gobject-base, cryptsetup-libs, krb5-libs, sqlite-libs, kmod-libs,
        libmodman, libarchive, enchant, libXfixes, systemd-libs, shared-mime-info,
        coreutils-common, libglvnd-glx, abattis-cantarell-fonts, cairo, audit-libs,
        libwayland-client, libpciaccess, sed, libgcc, libXrender, json-glib, libxshmfence,
        glib-networking, libdb, fedora-modular-repos, keyutils-libs, hwdata, glibc,
        libproxy, python3-pyparsing, device-mapper, libgpg-error, system-python, shadow-utils,
        libXtst, libstemmer, dbus-libs, libpng, cairo-gobject, libXau, pcre, python3-packaging,
        at-spi2-core, gawk, mesa-libglapi, libXinerama, adwaita-gtk2-theme, libX11-common,
        device-mapper-libs, python3-appdirs, libXrandr, bash, glibc-common, libselinux,
        elfutils-libs, libxkbcommon, libjpeg-turbo, libuuid, atk, acl, libmount, lz4-libs,
        ncurses, libgusb, glib2, python3, libpwquality, at-spi2-atk, libattr, libcrypt,
        gnome-themes-standard, libtiff, harfbuzz, libstdc++, libXcomposite, xkeyboard-config,
        libxcb, libnotify, systemd-pam, readline, libXxf86vm, python3-cairo, gtk-update-icon-cache,
        python3-pip, mesa-libEGL, zenity, python3-gobject, libXcursor, tzdata, gvfs-client,
        libverto, libblkid, cracklib, libusbx, libcroco, libdatrie, gdk-pixbuf2, libXi,
        qrencode-libs, python3-libs, graphite2, mesa-libwayland-egl, mesa-libGL, pixman,
        libXext, glibc-all-langpacks, info, grep, fedora-modular-release, setup, zlib,
        libtasn1, libepoxy, hunspell, libsemanage, python3-setuptools, fontpackages-filesystem,
        libsigsegv, hicolor-icon-theme, libxml2, expat, libgcrypt, emacs-filesystem,
        gsettings-desktop-schemas, chkconfig, xz-libs, mesa-libgbm, libthai, coreutils,
        colord-libs, libcap-ng, flatpak-runtime-config, elfutils-libelf, hunspell-en-GB,
        libsoup, pam, hunspell-en-US, jasper-libs, p11-kit-trust, avahi-libs, elfutils-default-yama-scope,
        libutempter, adwaita-icon-theme, ncurses-libs, libidn2, system-python-libs,
        libffi, libXdamage, libglvnd-egl, libXft, cups-libs, ustr, libcom_err, libappstream-glib,
        gnome-desktop3, gdk-pixbuf2-modules, libsepol, filesystem, gzip, mpfr]
  components:
    rpms: {}
  xmd:
    mbs: OMITTED
"""  # noqa

FLATPAK_RUNTIME_JSON = {
    "runtime": "org.fedoraproject.Platform",
    "runtime-version": "26",
    "sdk": "org.fedoraproject.Sdk",
    "cleanup-commands": ["touch -d @0 /usr/share/fonts",
                         "touch -d @0 /usr/share/fonts/*",
                         "fc-cache -fs"]
}
