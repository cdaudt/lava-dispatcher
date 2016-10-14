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


from lava_dispatcher.action import (
    Action,
    Pipeline,
    InfrastructureError,
)
from lava_dispatcher.menus.menus import (
    SelectorMenuAction,
    MenuConnect,
    MenuInterrupt,
    MenuReset
)
from lava_dispatcher.logical import Boot
from lava_dispatcher.power import ResetDevice
from lava_dispatcher.utils.strings import substitute
from lava_dispatcher.utils.network import dispatcher_ip
from lava_dispatcher.actions.boot import BootAction, AutoLoginAction
from lava_dispatcher.actions.boot.environment import ExportDeviceEnvironment
from lava_dispatcher.connections.lxc import ConnectLxc
from lava_dispatcher.actions.boot.fastboot import WaitForAdbDevice
from lava_dispatcher.utils.constants import DEFAULT_UEFI_LABEL_CLASS


class UefiMenu(Boot):
    """
    The UEFI Menu strategy selects the specified options
    and inserts relevant strings into the UEFI menu instead
    of issuing commands over a shell-like serial connection.
    """

    def __init__(self, parent, parameters):
        super(UefiMenu, self).__init__(parent)
        self.action = UefiMenuAction()
        self.action.section = self.action_type
        self.action.job = self.job
        parent.add_action(self.action, parameters)

    @classmethod
    def accepts(cls, device, parameters):
        if 'method' not in parameters:
            raise RuntimeError("method not specified in boot parameters")
        if parameters['method'] != 'uefi-menu':
            return False
        if 'boot' not in device['actions']:
            return False
        if 'methods' not in device['actions']['boot']:
            raise RuntimeError("Device misconfiguration")
        if 'uefi-menu' in device['actions']['boot']['methods']:
            params = device['actions']['boot']['methods']['uefi-menu']['parameters']
            if 'interrupt_prompt' in params and 'interrupt_string' in params:
                return True
        return False


class UEFIMenuInterrupt(MenuInterrupt):

    def __init__(self):
        super(UEFIMenuInterrupt, self).__init__()
        self.interrupt_prompt = None
        self.interrupt_string = None

    def validate(self):
        super(UEFIMenuInterrupt, self).validate()
        params = self.job.device['actions']['boot']['methods']['uefi-menu']['parameters']
        self.interrupt_prompt = params['interrupt_prompt']
        self.interrupt_string = params['interrupt_string']

    def run(self, connection, args=None):
        if not connection:
            self.logger.debug("%s called without active connection", self.name)
            return
        connection = super(UEFIMenuInterrupt, self).run(connection, args)
        connection.prompt_str = self.interrupt_prompt
        self.wait(connection)
        connection.raw_connection.send(self.interrupt_string)
        return connection


class UefiMenuSelector(SelectorMenuAction):

    def __init__(self):
        super(UefiMenuSelector, self).__init__()
        self.name = 'uefi-menu-selector'
        self.summary = 'select options in the uefi menu'
        self.description = 'select specified uefi menu items'
        self.selector.prompt = "Start:"
        self.boot_message = None

    def validate(self):
        """
        Setup the items and pattern based on the parameters for this
        specific action, then let the base class complete the validation.
        """
        params = self.job.device['actions']['boot']['methods']['uefi-menu']['parameters']
        if ('item_markup' not in params or
                'item_class' not in params or 'separator' not in params):
            self.errors = "Missing device parameters for UEFI menu operations"
        if 'commands' not in self.parameters:
            self.errors = "Missing commands in action parameters"
            return
        if self.parameters['commands'] not in self.job.device['actions']['boot']['methods']['uefi-menu']:
            self.errors = "Missing commands for %s" % self.parameters['commands']
        self.selector.item_markup = params['item_markup']
        self.selector.item_class = params['item_class']
        self.selector.separator = params['separator']
        if 'label_class' in params:
            self.selector.label_class = params['label_class']
        else:
            # label_class is problematic via jinja and yaml templating.
            self.selector.label_class = DEFAULT_UEFI_LABEL_CLASS
        self.selector.prompt = params['bootloader_prompt']  # initial prompt
        self.boot_message = params['boot_message']  # final prompt
        self.items = self.job.device['actions']['boot']['methods']['uefi-menu'][self.parameters['commands']]
        super(UefiMenuSelector, self).validate()

    def run(self, connection, args=None):
        if self.job.device.pre_os_command:
            self.logger.info("Running pre OS command.")
            command = self.job.device.pre_os_command
            if not self.run_command(command.split(' '), allow_silent=True):
                raise InfrastructureError("%s failed" % command)
        if not connection:
            return connection
        connection.prompt_str = self.selector.prompt
        self.logger.debug("Looking for %s", self.selector.prompt)
        self.wait(connection)
        connection = super(UefiMenuSelector, self).run(connection, args)
        self.logger.debug("Looking for %s", self.boot_message)
        connection.prompt_str = self.boot_message
        self.wait(connection)
        self.data['boot-result'] = 'failed' if self.errors else 'success'
        return connection


class UefiSubstituteCommands(Action):

    def __init__(self):
        super(UefiSubstituteCommands, self).__init__()
        self.name = 'uefi-commands'
        self.summary = 'substitute job values into uefi commands'
        self.description = 'set job-specific variables into the uefi menu commands'
        self.items = None

    def validate(self):
        super(UefiSubstituteCommands, self).validate()
        if self.parameters['commands'] not in self.job.device['actions']['boot']['methods']['uefi-menu']:
            self.errors = "Missing commands for %s" % self.parameters['commands']
        self.items = self.job.device['actions']['boot']['methods']['uefi-menu'][self.parameters['commands']]
        for item in self.items:
            if 'select' not in item:
                self.errors = "Invalid device configuration for %s: %s" % (self.name, item)

    def run(self, connection, args=None):
        connection = super(UefiSubstituteCommands, self).run(connection, args)
        try:
            ip_addr = dispatcher_ip()
        except InfrastructureError as exc:
            raise RuntimeError("Unable to get dispatcher IP address: %s" % exc)
        substitution_dictionary = {
            '{SERVER_IP}': ip_addr,
            '{RAMDISK}': self.get_common_data('file', 'ramdisk'),
            '{KERNEL}': self.get_common_data('file', 'kernel'),
            '{DTB}': self.get_common_data('file', 'dtb'),
            'TEST_MENU_NAME': "LAVA %s test image" % self.parameters['commands']
        }
        if 'download_action' in self.data and 'nfsrootfs' in self.data['download_action']:
            substitution_dictionary['{NFSROOTFS}'] = self.get_common_data('file', 'nfsroot')
        for item in self.items:
            if 'enter' in item['select']:
                item['select']['enter'] = substitute([item['select']['enter']], substitution_dictionary)[0]
            if 'items' in item['select']:
                # items is already a list, so pass without wrapping in []
                item['select']['items'] = substitute(item['select']['items'], substitution_dictionary)
        return connection


class UefiMenuAction(BootAction):

    def __init__(self):
        super(UefiMenuAction, self).__init__()
        self.name = 'uefi-menu-action'
        self.summary = 'interact with uefi menu'
        self.description = 'interrupt and select uefi menu items'

    def validate(self):
        super(UefiMenuAction, self).validate()
        self.set_common_data(
            'bootloader_prompt',
            'prompt',
            self.job.device['actions']['boot']['methods']['uefi-menu']['parameters']['bootloader_prompt']
        )

    def populate(self, parameters):
        self.internal_pipeline = Pipeline(parent=self, job=self.job, parameters=parameters)
        if 'commands' in parameters and 'fastboot' in parameters['commands']:
            self.internal_pipeline.add_action(UefiSubstituteCommands())
            self.internal_pipeline.add_action(MenuConnect())
            self.internal_pipeline.add_action(ResetDevice())
            self.internal_pipeline.add_action(UEFIMenuInterrupt())
            self.internal_pipeline.add_action(UefiMenuSelector())
            self.internal_pipeline.add_action(MenuReset())
            self.internal_pipeline.add_action(ConnectLxc())
            self.internal_pipeline.add_action(WaitForAdbDevice())
        else:
            self.internal_pipeline.add_action(UefiSubstituteCommands())
            self.internal_pipeline.add_action(MenuConnect())
            self.internal_pipeline.add_action(ResetDevice())
            self.internal_pipeline.add_action(UEFIMenuInterrupt())
            self.internal_pipeline.add_action(UefiMenuSelector())
            self.internal_pipeline.add_action(MenuReset())
            self.internal_pipeline.add_action(AutoLoginAction())
            self.internal_pipeline.add_action(ExportDeviceEnvironment())
