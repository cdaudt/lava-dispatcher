# Copyright (C) 2015 Linaro Limited
#
# Author: Neil Williams <neil.williams@linaro.org>
#
# This file is part of LAVA Dispatcher.
#
# LAVA Dispatcher is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# LAVA Dispatcher is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along
# with this program; if not, see <http://www.gnu.org/licenses>.


import os
from lava_dispatcher.logical import Deployment
from lava_dispatcher.action import Pipeline, Action
from lava_dispatcher.actions.deploy import DeployAction
from lava_dispatcher.actions.deploy.apply_overlay import ExtractRootfs, ExtractModules
from lava_dispatcher.actions.deploy.environment import DeployDeviceEnvironment
from lava_dispatcher.actions.deploy.overlay import OverlayAction
from lava_dispatcher.actions.deploy.download import DownloaderAction
from lava_dispatcher.utils.constants import DISPATCHER_DOWNLOAD_DIR
from lava_dispatcher.protocols.multinode import MultinodeProtocol

# Deploy SSH can mean a few options:
# for a primary connection, the device might need to be powered_on
# for a secondary connection, the device must be deployed
# In each case, files need to be copied to the device
# For primary: to: ssh is used to implicitly copy the authorization
# For secondary, authorize: ssh is needed as 'to' is already used.

# pylint: disable=too-many-instance-attributes


class Ssh(Deployment):
    """
    Copies files to the target to support further actions,
    typically the overlay.
    """

    compatibility = 1

    def __init__(self, parent, parameters):
        super(Ssh, self).__init__(parent)
        self.action = ScpOverlay()
        self.action.job = self.job
        parent.add_action(self.action, parameters)

    @classmethod
    def accepts(cls, device, parameters):
        if 'actions' not in device or 'deploy' not in device['actions']:
            return False
        if 'methods' not in device['actions']['deploy']:
            return False
        if 'ssh' not in device['actions']['deploy']['methods']:
            return False
        if 'to' in parameters and 'ssh' != parameters['to']:
            return False
        return True


class ScpOverlay(DeployAction):
    """
    Prepares the overlay and copies it to the target
    """
    def __init__(self):
        super(ScpOverlay, self).__init__()
        self.name = "scp-overlay"
        self.summary = "copy overlay to device"
        self.description = "prepare overlay and scp to device"
        self.section = 'deploy'
        self.items = []

    def validate(self):
        super(ScpOverlay, self).validate()
        self.items = [
            'firmware', 'kernel', 'dtb', 'rootfs', 'modules'
        ]
        lava_test_results_dir = self.parameters['deployment_data']['lava_test_results_dir']
        self.data['lava_test_results_dir'] = lava_test_results_dir % self.job.job_id

    def populate(self, parameters):
        self.internal_pipeline = Pipeline(parent=self, job=self.job, parameters=parameters)
        tar_flags = parameters['deployment_data']['tar_flags'] if 'tar_flags' in parameters['deployment_data'].keys() else ''
        self.set_common_data(self.name, 'tar_flags', tar_flags)
        self.internal_pipeline.add_action(OverlayAction())
        for item in self.items:
            if item in parameters:
                download = DownloaderAction(item, path=self.mkdtemp())
                download.max_retries = 3
                self.internal_pipeline.add_action(download, parameters)
                self.set_common_data('scp', item, True)
        # we might not have anything to download, just the overlay to push
        self.internal_pipeline.add_action(PrepareOverlayScp())
        # prepare the device environment settings in common data for enabling in the boot step
        self.internal_pipeline.add_action(DeployDeviceEnvironment())


class PrepareOverlayScp(Action):
    """
    Copy the overlay to the device using scp and then unpack remotely.
    Needs the device to be ready for SSH connection.
    """

    def __init__(self):
        super(PrepareOverlayScp, self).__init__()
        self.name = "prepare-scp-overlay"
        self.summary = "scp the overlay to the remote device"
        self.description = "copy the overlay over an existing ssh connection"
        self.host_keys = []

    def validate(self):
        super(PrepareOverlayScp, self).validate()
        lava_test_results_dir = self.parameters['deployment_data']['lava_test_results_dir']
        self.data['lava_test_results_dir'] = lava_test_results_dir % self.job.job_id
        environment = self.get_common_data('environment', 'env_dict')
        if not environment:
            environment = {}
        environment.update({"LC_ALL": "C.UTF-8", "LANG": "C"})
        self.set_common_data('environment', 'env_dict', environment)
        if 'protocols' in self.parameters:
            # set run to call the protocol, retrieve the data and store.
            for params in self.parameters['protocols'][MultinodeProtocol.name]:
                if isinstance(params, str):
                    self.errors = "Invalid protocol action setting - needs to be a list."
                    continue
                if 'action' not in params or params['action'] != self.name:
                    continue
                if 'messageID' not in params:
                    self.errors = "Invalid protocol block: %s" % params
                    return
                if 'message' not in params or not isinstance(params['message'], dict):
                    self.errors = "Missing message block for scp deployment"
                    return
                self.host_keys.append(params['messageID'])
        self.set_common_data(self.name, 'overlay', self.host_keys)

    def populate(self, parameters):
        self.internal_pipeline = Pipeline(parent=self, job=self.job, parameters=parameters)
        self.internal_pipeline.add_action(ExtractRootfs())  # idempotent, checks for nfsrootfs parameter
        self.internal_pipeline.add_action(ExtractModules())  # idempotent, checks for a modules parameter

    def run(self, connection, args=None):
        connection = super(PrepareOverlayScp, self).run(connection, args)
        self.logger.info("Preparing to copy: %s", os.path.basename(self.data['compress-overlay'].get('output')))
        self.set_common_data('scp-deploy', 'overlay', self.data['compress-overlay'].get('output'))
        for host_key in self.host_keys:
            data = self.get_common_data(MultinodeProtocol.name, host_key)
            if not data:
                self.logger.warning("Missing data for host_key %s", host_key)
                continue
            for params in self.parameters['protocols'][MultinodeProtocol.name]:
                replacement_key = [key for key, _ in params['message'].items() if key != 'yaml_line'][0]
                if replacement_key not in data:
                    self.logger.error("Mismatched replacement key %s and received data %s",
                                      replacement_key, list(data.keys()))
                    continue
                self.set_common_data(self.name, host_key, str(data[replacement_key]))
                self.logger.info("data %s replacement key is %s", host_key, self.get_common_data(self.name, host_key))
        return connection
