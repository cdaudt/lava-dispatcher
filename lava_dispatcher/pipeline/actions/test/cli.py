# Copyright (C) 2017 Linaro Limited
#
# Author: Edmund Szeto <edmund.szeto@cypress.com>
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

import re
import pexpect
import time
import decimal

from collections import OrderedDict
from lava_dispatcher.pipeline.action import (
    Pipeline,
    TestError,
    Timeout,
    InfrastructureError,
)
from lava_dispatcher.pipeline.actions.test import (
    TestAction,
)
from lava_dispatcher.pipeline.logical import (
    LavaTest,
    RetryAction,
)


class TestCli(LavaTest):
    """
    LavaTestCli Strategy object
    """
    def __init__(self, parent, parameters):
        super(TestCli, self).__init__(parent)
        self.action = TestCliAction()
        self.action.job = self.job
        self.action.section = self.action_type
        parent.add_action(self.action, parameters)

    @classmethod
    def accepts(cls, device, parameters):
        # TODO: Add configurable timeouts
        required_parms = ['name', 'sequence']
        if 'cli_tests' in parameters:
            for test in parameters['cli_tests']:
                if all([x for x in required_parms if x in test]):
                    return True
        else:
            return False

    @classmethod
    def needs_deployment_data(cls):
        return True

    @classmethod
    def needs_overlay(cls):
        return False

    @classmethod
    def has_shell(cls):
        return False


class TestCliRetry(RetryAction):

    def __init__(self):
        super(TestCliRetry, self).__init__()
        self.description = "Retry wrapper for lava-test-cli"
        self.summary = "Retry support for Lava Test CLI"
        self.name = "lava-test-cli-retry"

    def populate(self, parameters):
        self.internal_pipeline = Pipeline(parent=self, job=self.job, parameters=parameters)
        self.internal_pipeline.add_action(TestCliAction())


class TestCliAction(TestAction):  # pylint: disable=too-many-instance-attributes
    """
    Runs a collection of test sequences, which include commands and checks.
    """

    def __init__(self):
        super(TestCliAction, self).__init__()
        self.description = "Executing lava-test-cli"
        self.summary = "Lava Test CLI"
        self.name = "lava-test-cli"
        self.test_suite_name = None
        self.report = {}
        self.fixupdict = {}
        self.patterns = {}
        self.sequence = []
        self.prompt_str = None

    def validate(self):
        super(TestCliAction, self).validate()

    def run(self, connection, max_end_time, args=None):
        """
        Common run function for subclasses which define custom patterns
        boot-result is a simple sanity test and only supports the most recent boot
        just to allow the test action to know if something has booted. Failed boots will timeout.
        A missing boot-result could be a missing deployment for some actions.
        """
        # Sanity test: could be a missing deployment for some actions
        res = self.get_namespace_data(action='boot', label='shared', key='boot-result')
        if not res:
            raise RuntimeError("No boot action result found")
        connection = super(TestCliAction, self).run(connection, max_end_time, args)

        # Get the connection, specific to this namespace
        connection = self.get_namespace_data(
            action='shared', label='shared', key='connection', deepcopy=False)

        res = self.get_namespace_data(action='boot', label='shared', key='boot-result')
        if res != "success":
            self.logger.debug("Skipping CLI test - previous boot attempt was not successful.")
            self.results.update({self.name: "skipped"})
            # FIXME: with predictable UID, could set each test definition metadata to "skipped"
            return connection

        if not connection:
            raise InfrastructureError("Connection closed")

        self.prompt_str = connection.prompt_str

        # Take full control of the connection
        with connection.test_connection() as test_connection:
            for test in self.parameters['cli_tests']:
                self.test_suite_name = test['name']

                self.sequence = test.get('sequence')
                self._process_sequence(test_connection)

        self.results.update({'status': 'passed'})
        return connection

    def _process_sequence(self, test_connection):
        for step in self.sequence:
            # Only one first key,value pair in the dict is of interest
            if 'commands' in step:
                self.logger.info("commands={}".format(step['commands']))
                for command in step['commands']:
                    self._wait_for_prompt(test_connection)
                    if command is None:
                        self.logger.info("Sending newline")
                        test_connection.sendline("")
                    else:
                        self.logger.info("Sending command '{}'".format(command))
                        test_connection.sendline(command)
            elif 'inputs' in step:
                self.logger.info("inputs={}".format(step['inputs']))
                for user_input in step['inputs']:
                    if user_input is None:
                        self.logger.info("Sending newline")
                        test_connection.sendline("")
                    else:
                        self.logger.info("Sending input '{}'"
                                         .format(user_input))
                        test_connection.sendline(user_input)

            elif 'check' in step:
                self.logger.info("check={}".format(step['check']))

                # Create a dictionary of regex patterns and end conditions
                self.patterns = OrderedDict()
                self.patterns['eof'] = pexpect.EOF
                self.patterns['timeout'] = pexpect.TIMEOUT

                if 'pass' in step['check']:
                    for i,condition in enumerate(step['check']['pass']):
                        self.patterns['pass{}'.format(i)] = condition

                if 'fail' in step['check']:
                    for i,condition in enumerate(step['check']['fail']):
                        self.patterns['fail{}'.format(i)] = condition

                if 'measure' in step['check']:
                    self.patterns['measure'] = step['check']['measure']['pattern']

                # Keep running until we hit an exit condition (EOF, timeout,
                # pass, or fail)
                while self._keep_running(test_connection, step['check'],
                                         timeout=test_connection.timeout):
                    pass
            elif 'sleep' in step:
                self.logger.info("sleep={}".format(step['sleep']))
                sleep = Timeout(self.name, Timeout.parse(step['sleep']))
                self.logger.info("sleep for {} s".format(sleep.duration))
                time.sleep(sleep.duration)
            elif 'control' in step:
                self.logger.info("control={}".format(step['control']))
                test_connection.sendcontrol(step['control'])
            else:
                raise RuntimeError("Unsupported sequence")
            continue

    def _wait_for_prompt(self, test_connection, poke=False, timeout=None):
        if self.prompt_str is None:
            raise RuntimeError("No connection prompt string set")

        if timeout is None:
            timeout = test_connection.timeout

        if poke:
            # Send a newline after every 1/4th of timeout
            timeout = timeout / 4.0
            poke_count = 0

        while True:
            try:
                self.logger.debug("Waiting for prompt {}"
                                  .format(self.prompt_str))
                return test_connection.expect(self.prompt_str, timeout=timeout)
            except TestError:
                if poke and poke_count < 3:
                    self.logger.info("Sending newline to trigger prompt")
                    poke_count += 1
                    # Send a newline
                    test_connection.sendline("")
                    continue
                else:
                    raise
            except KeyboardInterrupt:
                raise KeyboardInterrupt

    def _keep_running(self, test_connection, check, timeout=120):
        self.logger.debug("CLI test timeout: %d seconds", timeout)
        # retval from expect() is index of item in list that matched
        retval = test_connection.expect(list(self.patterns.values()),
                                        timeout=timeout)
        return self.check_patterns(list(self.patterns.keys())[retval],
                                   test_connection, check)

    def check_patterns(self, event, test_connection, check):  # pylint: disable=too-many-branches
        """
        Defines the base set of pattern responses.
        Stores the results of testcases inside the TestAction
        Call from subclasses before checking subclass-specific events.
        """

        # Get the re.MatchObject
        match = test_connection.match

        name = self.test_suite_name.replace(' ', '-').lower()
        results = {
            'definition': name,
            'extra': {}
        }

        if 'name' in check:
            results['case'] = check['name'].replace(' ', '-').lower()
        else:
            results['case'] = name

        keep_running = False

        if event == "eof":
            self.logger.warning("CLI test got connection EOF")
            self.errors = "lava cli test connection EOF"
            self.results.update({'status': 'failed'})
        elif event == "timeout":
            self.logger.warning("CLI test timed out")
            self.errors = "lava cli test has timed out"
            self.results.update({'status': 'failed'})
        elif "pass" in event:
            results['result'] = 'pass'
            results['extra'].update({'match': match.group(0)})
            self.logger.results(results)
        elif "fail" in event:
            results['result'] = 'fail'
            results['extra'].update({'match': match.group(0)})
            self.logger.results(results)
        elif event == "measure":
            self._check_measure(match, check, results)
        else:
            self.logger.info("Unhandled event '{}'".format(event))
        return keep_running

    def _check_measure(self, match, check, results):
        results['extra'].update({'match': match.group(0)})

        eval_dict = match.groupdict()

        if 'measurement' in match.groupdict():
             measurement = match.groupdict()['measurement']
        else:
            # Assume first match group is the measurement
            measurement = match.group(1)
            eval_dict.update({'measurement': measurement})

        # Measurements can only be numbers
        try:
            measurement = decimal.Decimal(measurement)
        except:
            raise TestError("Invalid measurement %s", measurement)

        results['measurement'] = measurement;

        if 'units' in check['measure']:
            results['units'] = check['measure']['units']
            eval_dict.update({'units': check['measure']['units']})
        elif 'units' in match.groupdict():
            results['units'] = match.groupdict()['units']
        if 'pass' in check['measure']:
            self._check_expression(results, check['measure']['pass'], eval_dict,
                                   'pass')
        elif 'fail' in check['measure']:
            self._check_expression(results, check['measure']['fail'], eval_dict,
                                   'fail')
        else:
            # Just a measurement, so keep looking for pass/fail conditions
            results['result'] = 'pass'
            keep_running = True

        self.logger.results(results)

    def _check_expression(self, results, expr, eval_dict, cond):
        self.logger.info("Checking '{}' expression '{}'".format(cond, expr))

        results['extra'].update({'measurement \'{}\' expression'.format(cond): 
                                 expr})

        if self._validate_check_expression(expr, eval_dict.keys()):
            try:
                if eval(expr,
                        {'__builtins__': {}, 'True': True, 'False': False},
                        eval_dict):
                    results['result'] = cond
                else:
                    results['result'] = 'fail' if cond == 'pass' else 'pass'
            except:
                self.logger.error("Failed to evaluate '{}' expression '{}'"
                                  .format(cond, expr))
                results['result'] = 'fail'
                results['extra'].update({'error':
                                         'failed to evaluate \'{}\' expression \'{}\''
                                         .format(cond, expr)})
        else:
            self.logger.error("Failed to validate '{}' expression".format(cond))
            results['result'] = 'fail'
            results['extra'].update({'error':
                                     'failed to validate \'{}\' expression \'{}\''
                                     .format(cond, expr)})

    def _validate_check_expression(self, expression, allowed):
        self.logger.debug("pass/fail expression '{}' (allowed='{}')"
                          .format(expression, allowed))

        ops1 = ['(', ')', '>=', '==', '<=', '>', '<']
        ops2 = ['and', 'or', 'True', 'False']

        # Remove all allowed operators and constants
        leftover = expression
        for i in ops1:
            leftover = leftover.replace(i, ' ')
        for i in ops2:
            leftover = re.sub(r'\b' + i + r'\b', '', leftover)

        self.logger.debug("pass/fail expression without operators '{}'"
                           .format(leftover))

        # Remove all numerical values with format ".n+", e.g., .1, .223, .001
        leftover = re.sub(r'\B\.\d+\b', '', leftover)

        # Remove all numerical values with format "n.n", e.g., 1.1, 110.223
        leftover = re.sub(r'\b[\d.]+\b','', leftover)

        # Remove all string values (in single or double quotes)
        leftover = re.sub(r'\B\'[\w ./-_]+\'\B', '', leftover)
        leftover = re.sub(r'\B\"[\w ./-_]+\"\B', '', leftover)

        self.logger.debug("pass/fail expression without operators, numerical and string values '{}'"
                          .format(leftover))

        # Check the leftovers to make sure they are in the 'allowed' list
        for i in leftover.split():
            if i not in allowed:
                self.logger.debug("pass/fail expression variable '{}' not allowed"
                                  .format(i))
                return False

        return True
