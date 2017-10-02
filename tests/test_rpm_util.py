"""
Copyright (c) 2017 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""

from __future__ import absolute_import, print_function

from atomic_reactor.rpm_util import parse_rpm_output

FAKE_SIGMD5 = b'0' * 32
FAKE_SIGNATURE = "RSA/SHA256, Tue 30 Aug 2016 00:00:00, Key ID 01234567890abc"


def test_parse_rpm_output():
    res = parse_rpm_output([
        "name1;1.0;1;x86_64;0;2000;" + FAKE_SIGMD5.decode() + ";23000;" +
        FAKE_SIGNATURE + ";(none)",
        "name2;2.0;1;x86_64;0;3000;" + FAKE_SIGMD5.decode() + ";24000;" +
        "(none);" + FAKE_SIGNATURE,
        "gpg-pubkey;64dab85d;57d33e22;(none);(none);0;(none);1473461794;(none);(none)",
    ])

    assert res == [
        {
            'type': 'rpm',
            'name': 'name1',
            'version': '1.0',
            'release': '1',
            'arch': 'x86_64',
            'epoch': 0,
            'sigmd5': FAKE_SIGMD5.decode(),
            'signature': "01234567890abc",
        },
        {
            'type': 'rpm',
            'name': 'name2',
            'version': '2.0',
            'release': '1',
            'arch': 'x86_64',
            'epoch': 0,
            'sigmd5': FAKE_SIGMD5.decode(),
            'signature': "01234567890abc",
        }
    ]

    # Tests with different fields and separator
    res = parse_rpm_output(["1|1.0|name1"],
                           tags=['RELEASE', 'VERSION', 'NAME'],
                           separator="|")

    assert res == [
        {
            'type': 'rpm',
            'name': 'name1',
            'version': '1.0',
            'release': '1',
            'arch': None,
            'epoch': None,
            'sigmd5': None,
            'signature': None,
        }
    ]
