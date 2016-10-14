# Copyright (C) 2014 Linaro Limited
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

from lava_dispatcher.action import Action
from lava_dispatcher.logical import RetryAction
from lava_dispatcher.utils.constants import (
    AUTOLOGIN_DEFAULT_TIMEOUT,
    DEFAULT_SHELL_PROMPT,
    DISTINCTIVE_PROMPT_CHARACTERS,
)
from lava_dispatcher.utils.shell import wait_for_prompt
from lava_dispatcher.utils.messages import LinuxKernelMessages


class BootAction(RetryAction):
    """
    Base class for all actions which control power-on
    and boot behaviour of a device under test.
    The subclass selected to do the work will be the
    subclass returning True in the accepts(device_type, image)
    function.
    Each new subclass needs a unit test to ensure it is
    reliably selected for the correct job and not
    selected for an invalid job or a job
    accepted by a different subclass.

    Boot and Test are closely related - a fail error in Boot
    will cause subsequent Test actions to be skipped.
    """

    name = 'boot'


# FIXME: needs a unit test to check YAML parameter syntax
class AutoLoginAction(Action):
    """
    Automatically login on the device.
    If 'auto_login' is not present in the parameters, this action does nothing.

    This Action expect POSIX-compatible support of PS1 from shell
    """
    def __init__(self):
        super(AutoLoginAction, self).__init__()
        self.name = 'auto-login-action'
        self.description = "automatically login after boot using job parameters"
        self.summary = "Auto-login after boot"
        self.check_prompt_characters_warning = (
            "The string '%s' does not look like a typical prompt and"
            " could match status messages instead. Please check the"
            " job log files and use a prompt string which matches the"
            " actual prompt string more closely."
        )
        # FIXME: self.timeout.duration = AUTOLOGIN_DEFAULT_TIMEOUT

    def validate(self):  # pylint: disable=too-many-branches
        super(AutoLoginAction, self).validate()
        # Skip auto login if the configuration is not found
        params = self.parameters.get('auto_login', None)
        if params:
            if not isinstance(params, dict):
                self.errors = "'auto_login' should be a dictionary"
                return

            if 'login_prompt' not in params:
                self.errors = "'login_prompt' is mandatory for auto_login"
            if not params['login_prompt']:
                self.errors = "Value for 'login_prompt' cannot be empty"
            if 'username' not in params:
                self.errors = "'username' is mandatory for auto_login"

            if 'password_prompt' in params:
                if 'password' not in params:
                    self.errors = "'password' is mandatory if 'password_prompt' is used in auto_login"

        prompts = self.parameters.get('prompts', None)
        if prompts is None:
            self.errors = "'prompts' is mandatory for AutoLoginAction"

        if not isinstance(prompts, (list, str)):
            self.errors = "'prompts' should be a list or a str"

        if not prompts:
            self.errors = "Value for 'prompts' cannot be empty"

        if isinstance(prompts, list):
            for prompt in prompts:
                if not prompt:
                    self.errors = "Items of 'prompts' can't be empty"

    def run(self, connection, args=None):
        # Prompts commonly include # - when logging such strings,
        # use lazy logging or the string will not be quoted correctly.
        def check_prompt_characters(prompt):
            if not any([True for c in DISTINCTIVE_PROMPT_CHARACTERS if c in prompt]):
                self.logger.warning(self.check_prompt_characters_warning, prompt)

        connection.prompt_str = LinuxKernelMessages.get_init_prompts()
        # Skip auto login if the configuration is not found
        params = self.parameters.get('auto_login', None)
        if params is None:
            self.logger.debug("Skipping of auto login")
        else:
            self.logger.debug("Waiting for the login prompt")
            connection.prompt_str.append(params['login_prompt'])
            self.logger.debug(connection.prompt_str)
            results = LinuxKernelMessages.parse_failures(connection)
            if len(results) > 1:
                self.results = {'fail': results}
                return connection
            else:
                connection.sendline(params['username'], delay=self.character_delay)

            if 'password_prompt' in params:
                self.logger.debug("Waiting for password prompt")
                connection.prompt_str = params['password_prompt']
                self.wait(connection)
                connection.sendline(params['password'], delay=self.character_delay)
        # prompt_str can be a list or str
        if isinstance(connection.prompt_str, str):
            connection.prompt_str = [DEFAULT_SHELL_PROMPT]
        else:
            connection.prompt_str.extend([DEFAULT_SHELL_PROMPT])

        prompts = self.parameters.get('prompts', None)
        if isinstance(prompts, list):
            for prompt in prompts:
                check_prompt_characters(prompt)
            connection.prompt_str.extend(prompts)
        else:
            check_prompt_characters(prompts)
            connection.prompt_str.append(prompts)

        self.logger.debug("Setting shell prompt(s) to %s" % connection.prompt_str)  # pylint: disable=logging-not-lazy

        if params is None:
            self.logger.debug("Parsing kernel messages")
            parsed = LinuxKernelMessages.parse_failures(connection)
            if len(parsed) and 'success' in parsed[0]:
                self.results = {'success': parsed[0]}
            else:
                self.results = {'fail': parsed}
        connection.sendline('export PS1="%s"' % DEFAULT_SHELL_PROMPT, delay=self.character_delay)

        return connection
