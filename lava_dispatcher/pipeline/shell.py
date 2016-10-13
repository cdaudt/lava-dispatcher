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

import contextlib
import os
import pexpect
import sys
import time
from lava_dispatcher.pipeline.action import (
    Action,
    JobError,
    TestError,
    InfrastructureError,
    Timeout,
)
from lava_dispatcher.pipeline.connection import Connection, CommandRunner
from lava_dispatcher.pipeline.utils.constants import (
    SHELL_SEND_DELAY,
    LINE_SEPARATOR
)


class ShellLogger(object):
    """
    Builds a YAML log message out of the incremental output of the pexpect.spawn
    using the logfile support built into pexpect.
    """

    def __init__(self, logger):
        self.line = ''
        self.logger = logger

    def write(self, new_line):
        replacements = {
            '\n\n': '\n',  # double lines to single
            '\r': '',
            '"': '\\\"',  # escape double quotes for YAML syntax
            '\x1b': ''  # remove escape control characters
        }
        for key, value in replacements.items():
            new_line = new_line.replace(key, value)
        lines = self.line + new_line

        # Print one full line at a time. A partial line is kept in memory.
        if '\n' in lines:
            last_ret = lines.rindex('\n')
            self.line = lines[last_ret + 1:]
            lines = lines[:last_ret]
            for line in lines.split('\n'):
                self.logger.target(line)
        else:
            self.line = lines
        return

    def flush(self):  # pylint: disable=no-self-use
        sys.stdout.flush()
        sys.stderr.flush()

    def __del__(self):
        # Only needed for processes that does not end output with a new line.
        if self.line:
            self.write('\n')


class ShellCommand(pexpect.spawn):  # pylint: disable=too-many-public-methods
    """
    Run a command over a connection using pexpect instead of
    subprocess, i.e. not on the dispatcher itself.
    Takes a Timeout object (to support overrides and logging)

    A ShellCommand is a raw_connection for a ShellConnection instance.
    """

    def __init__(self, command, lava_timeout, logger=None, cwd=None):
        if not lava_timeout or not isinstance(lava_timeout, Timeout):
            raise RuntimeError("ShellCommand needs a timeout set by the calling Action")
        if not logger:
            raise RuntimeError("ShellCommand needs a logger")
        if sys.version >= '3':
            # Set the encoding to get unicode strings.
            # This is only available in pexpect>=4
            pexpect.spawn.__init__(
                self, command,
                timeout=lava_timeout.duration,
                cwd=cwd,
                logfile=ShellLogger(logger),
                encoding="utf-8"
            )
        else:
            # TODO: to be removed when using pexpect>=4
            # Keep the old behavior for python2
            pexpect.spawn.__init__(
                self, command,
                timeout=lava_timeout.duration,
                cwd=cwd,
                logfile=ShellLogger(logger),
            )

        self.name = "ShellCommand"
        self.logger = logger
        # os.linesep is based on the interpreter running the dispatcher, not the target device
        self.linesep = LINE_SEPARATOR
        # serial can be slow, races do funny things, so allow for a delay
        self.delaybeforesend = SHELL_SEND_DELAY
        self.lava_timeout = lava_timeout

    def sendline(self, s='', delay=0, send_char=True):  # pylint: disable=arguments-differ
        """
        Extends pexpect.sendline so that it can support the delay argument which allows a delay
        between sending each character to get around slow serial problems (iPXE).
        pexpect sendline does exactly the same thing: calls send for the string then os.linesep.

        :param s: string to send
        :param delay: delay in milliseconds between sending each character
        :param send_char: send one character or entire string
        """
        if delay:
            self.logger.debug({"sending": s, "delay": "%s millisecond" % delay})
        else:
            self.logger.debug({"sending": s})
        self.send(s, delay, send_char)
        self.send(self.linesep, delay)

    def sendcontrol(self, char):
        self.logger.debug("sendcontrol: %s", char)
        return super(ShellCommand, self).sendcontrol(char)

    # FIXME: no sense in sending delay and send_char - if delay is non-zero, send_char needs to be True
    def send(self, string, delay=0, send_char=True):  # pylint: disable=arguments-differ
        """
        Extends pexpect.send to support extra arguments, delay and send by character flags.
        """
        sent = 0
        delay = float(delay) / 1000
        if send_char:
            for char in string:
                sent += super(ShellCommand, self).send(char)
                time.sleep(delay)
        else:
            sent = super(ShellCommand, self).send(string)
        return sent

    def expect(self, *args, **kw):
        """
        No point doing explicit logging here, the SignalDirector can help
        the TestShellAction make much more useful reports of what was matched
        """
        try:
            proc = super(ShellCommand, self).expect(*args, **kw)
        except pexpect.TIMEOUT:
            raise TestError("ShellCommand command timed out.")
        except ValueError as exc:
            raise TestError(exc)
        except pexpect.EOF:
            # FIXME: deliberately closing the connection (and starting a new one) needs to be supported.
            raise InfrastructureError("Connection closed")
        return proc

    def empty_buffer(self):
        """Make sure there is nothing in the pexpect buffer."""
        index = 0
        while index == 0:
            index = self.expect(['.+', pexpect.EOF, pexpect.TIMEOUT], timeout=1)


class ShellSession(Connection):

    def __init__(self, job, shell_command):
        """
        The connection takes over result handling for the TestAction, adding individual results to the
        logs every time a test_case is matched, so that if a test definition falls over or times out,
        the results so-far will be retained.
        Each result generates an item in the data context with an ID. This ID can be used later to
        look up each individial testcase result.
        TODO: ensure the stdout for each testcase result is captured and tagged with this ID.

        A ShellSession uses a CommandRunner. Other connections would need to add their own
        support.
        """
        super(ShellSession, self).__init__(job, shell_command)
        self.__runner__ = None
        self.name = "ShellSession"
        self.data = job.context
        # FIXME: rename __prompt_str__ to indicate it can be a list or str
        self.__prompt_str__ = None
        self.spawn = shell_command
        self.timeout = shell_command.lava_timeout

    def disconnect(self, reason):
        # FIXME
        pass

    # FIXME: rename prompt_str to indicate it can be a list or str
    @property
    def prompt_str(self):
        return self.__prompt_str__

    @prompt_str.setter
    def prompt_str(self, string):
        # FIXME: Debug logging should show whenever this property is changed
        self.__prompt_str__ = string
        if self.__runner__:
            self.__runner__.change_prompt(self.__prompt_str__)

    @property
    def runner(self):
        if self.__runner__ is None:
            # device = self.device
            spawned_shell = self.raw_connection  # ShellCommand(pexpect.spawn)
            # FIXME: the prompts should not be needed here, only kvm uses these. Remove.
            # prompt_str = parameters['prompts']
            prompt_str_includes_rc = True  # FIXME - parameters['deployment_data']['TESTER_PS1_INCLUDES_RC']?
#            prompt_str_includes_rc = device.config.tester_ps1_includes_rc
            # The Connection for a CommandRunner in the pipeline needs to be a ShellCommand, not logging_spawn
            self.__runner__ = CommandRunner(spawned_shell, self.prompt_str, prompt_str_includes_rc)
        return self.__runner__

    def run_command(self, command):
        self.runner.run(command)

    @contextlib.contextmanager
    def test_connection(self):
        """
        Yields the actual connection which can be used to interact inside this shell.
        """
        if self.__runner__ is None:
            spawned_shell = self.raw_connection  # ShellCommand(pexpect.spawn)
            # prompt_str = parameters['prompts']
            prompt_str_includes_rc = True  # FIXME - do we need this?
#            prompt_str_includes_rc = device.config.tester_ps1_includes_rc
            # The Connection for a CommandRunner in the pipeline needs to be a ShellCommand, not logging_spawn
            self.__runner__ = CommandRunner(spawned_shell, self.prompt_str,
                                            prompt_str_includes_rc)
        yield self.__runner__.get_connection()

    def wait(self):
        if not self.prompt_str:
            self.prompt_str = self.check_char
        try:
            return self.runner.wait_for_prompt(self.timeout.duration, self.check_char)
        except pexpect.TIMEOUT:
            raise JobError("wait for prompt timed out")


class SimpleSession(ShellSession):

    def wait(self):
        """
        Simple wait without sendling blank lines as that causes the menu
        to advance without data which can cause blank entries and can cause
        the menu to exit to an unrecognised prompt.
        """
        try:
            return self.raw_connection.expect(self.prompt_str, timeout=self.timeout.duration)
        except pexpect.TIMEOUT:
            raise JobError("wait for prompt timed out")
        except KeyboardInterrupt:
            raise KeyboardInterrupt


class ExpectShellSession(Action):
    """
    Waits for a shell connection to the device for the current job.
    The shell connection can be over any particular connection,
    all that is needed is a prompt.
    """
    compatibility = 2

    def __init__(self):
        super(ExpectShellSession, self).__init__()
        self.name = "expect-shell-connection"
        self.summary = "Expect a shell prompt"
        self.description = "Wait for a shell"

    def validate(self):
        super(ExpectShellSession, self).validate()
        if 'prompts' not in self.parameters:
            self.errors = "Unable to identify test image prompts from parameters."

    def run(self, connection, args=None):
        connection = super(ExpectShellSession, self).run(connection, args)
        if not connection:
            raise JobError("No connection available.")
        if not connection.prompt_str:
            connection.prompt_str = self.parameters['prompts']
        self.logger.debug("%s: Waiting for prompt %s", self.name, ', '.join(self.parameters['prompts']))
        self.wait(connection)  # FIXME: should this be a regular RetryAction operation?
        return connection
