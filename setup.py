#!/usr/bin/env python

import sys
from setuptools import setup, find_packages
from version import version_tag

if sys.version_info[0] == 2:
    lzma = 'pyliblzma >= 0.5.3'
elif sys.version_info[0] == 3:
    lzma = ''

setup(
    name="lava-dispatcher",
    version=version_tag(),
    description="Linaro Automated Validation Architecture dispatcher",
    url='https://git.linaro.org/lava/lava-dispatcher.git',
    author='Linaro Validation Team',
    author_email='linaro-validation@lists.linaro.org',
    license='GPL v2 or later',
    classifiers=[
        'Development Status :: 5 - Production/Stable',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: GNU General Public License v2 or later (GPLv2+)',
        'Operating System :: POSIX :: Linux',
        'Programming Language :: Python :: 2.7',
        'Topic :: Software Development :: Embedded Systems',
        'Topic :: Software Development :: Testing',
    ],
    entry_points="""
    [lava.commands]
    dispatch = lava.dispatcher.commands:dispatch
    """,
    packages=find_packages(),
    namespace_packages=['lava'],
    package_data={
        'lava_dispatcher': [
            'device_types/*.conf',
            'devices/*.yaml',
            'device/dynamic_vm_keys/lava*',
            'lava_test_shell/lava-background-process-start',
            'lava_test_shell/lava-background-process-stop',
            'lava_test_shell/lava-echo-ipv4',
            'lava_test_shell/lava-vm-groups-setup-host',
            'lava_test_shell/lava-installed-packages',
            'lava_test_shell/lava-os-build',
            'lava_test_shell/lava-test-case',
            'lava_test_shell/lava-test-case-attach',
            'lava_test_shell/lava-test-case-metadata',
            'lava_test_shell/lava-test-run-attach',
            'lava_test_shell/lava-test-runner',
            'lava_test_shell/lava-test-set',
            'lava_test_shell/lava-test-shell',
            'lava_test_shell/multi_node/*',
            'lava_test_shell/vland/*',
            'lava_test_shell/lmp/*',
            'lava_test_shell/distro/fedora/*',
            'lava_test_shell/distro/android/*',
            'lava_test_shell/distro/ubuntu/*',
            'lava_test_shell/distro/debian/*',
            'lava_test_shell/distro/oe/*',
        ],
    },
    install_requires=[
        'pexpect >= 2.3',
        'PyYAML',
        '%s' % lzma,
        'requests',
        'netifaces >= 0.10.0',
        'nose',
        'pyzmq',
        'configobj'
    ],
    test_suite='lava_dispatcher.test',
    tests_require=[
        'pep8 >= 1.4.6',
        'testscenarios >= 0.4'
    ],
    data_files=[
        ('/usr/share/lava-dispatcher/',
            ['etc/tftpd-hpa']),
        ('/etc/exports.d',
            ['etc/lava-dispatcher-nfs.exports']),
        ('/etc/modprobe.d',
            ['etc/lava-options.conf']),
        ('/etc/modules-load.d/',
            ['etc/lava-modules.conf']),
        ('/etc/logrotate.d/',
            ['etc/logrotate.d/lava-slave-log']),
        ('/etc/init.d/',
            ['etc/lava-slave.init']),
        ('/usr/share/lava-dispatcher/',
            ['etc/lava-slave.service'])
    ],
    scripts=[
        'lava/dispatcher/lava-dispatch',
        'lava/dispatcher/lava-dispatcher-slave',
        'lava/dispatcher/lava-slave'
    ],
)
