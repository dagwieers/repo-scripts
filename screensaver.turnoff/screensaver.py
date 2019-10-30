# -*- coding: utf-8 -*-
# Copyright: (c) 2019, Dag Wieers (@dagwieers) <dag@wieers.com>
# GNU General Public License v2.0 (see COPYING or https://www.gnu.org/licenses/gpl-2.0.txt)
''' This Kodi addon turns off display devices when Kodi goes into screensaver-mode '''

from __future__ import absolute_import, division, unicode_literals
import sys
import atexit
import subprocess

from xbmc import executebuiltin, executeJSONRPC, log as xlog, Monitor
from xbmcaddon import Addon
from xbmcgui import Dialog, WindowXMLDialog

DEBUG_LOGGING = 0

# NOTE: The below order relates to resources/settings.xml
DISPLAY_METHODS = [
    dict(name='do-nothing', title='Do nothing',
         function='log',
         args_off=[2, 'Do nothing to power off display'],
         args_on=[2, 'Do nothing to power back on display']),
    dict(name='cec-builtin', title='CEC (buil-in)',
         function='run_builtin',
         args_off=['CECStandby'],
         args_on=['CECActivateSource']),
    dict(name='no-signal-rpi', title='No Signal on Raspberry Pi (using vcgencmd)',
         function='run_command',
         args_off=['vcgencmd', 'display_power', '0'],
         args_on=['vcgencmd', 'display_power', '1']),
    dict(name='dpms-builtin', title='DPMS (built-in)',
         function='run_builtin',
         args_off=['ToggleDPMS'],
         args_on=['ToggleDPMS']),
    dict(name='dpms-xset', title='DPMS (using xset)',
         function='run_command',
         args_off=['xset', 'dpms', 'force', 'off'],
         args_on=['xset', 'dpms', 'force', 'on']),
    dict(name='dpms-vbetool', title='DPMS (using vbetool)',
         function='run_command',
         args_off=['vbetool', 'dpms', 'off'],
         args_on=['vbetool', 'dpms', 'on']),
    # TODO: This needs more outside testing
    dict(name='dpms-xrandr', title='DPMS (using xrandr)',
         function='run_command',
         args_off=['xrandr', '--output CRT-0', 'off'],
         args_on=['xrandr', '--output CRT-0', 'on']),
    # TODO: This needs more outside testing
    dict(name='cec-android', title='CEC on Android (kernel)',
         function='run_command',
         args_off=['su', '-c', 'echo 0 >/sys/devices/virtual/graphics/fb0/cec'],
         args_on=['su', '-c', 'echo 1 >/sys/devices/virtual/graphics/fb0/cec']),
    # NOTE: Contrary to what one might think, 1 means off and 0 means on
    dict(name='backlight-rpi', title='Backlight on Raspberry Pi (kernel)',
         function='run_command',
         args_off=['su', '-c', 'echo 1 >/sys/class/backlight/rpi_backlight/bl_power'],
         args_on=['su', '-c', 'echo 0 >/sys/class/backlight/rpi_backlight/bl_power']),
    dict(name='backlight-odroid-c2', title='Backlight on Odroid C2 (kernel)',
         function='run_command',
         args_off=['su', '-c', 'echo 0 >/sys/class/amhdmitx/amhdmitx0/phy'],
         args_on=['su', '-c', 'echo 1 >/sys/class/amhdmitx/amhdmitx0/phy']),
]

POWER_METHODS = [
    dict(name='do-nothing', title='Do nothing',
         function='log', kwargs_off=dict(level=2, msg='Do nothing to power off system')),
    dict(name='suspend-builtin', title='Suspend (built-in)',
         function='jsonrpc', kwargs_off=dict(method='System.Suspend')),
    dict(name='hibernate-builtin', title='Hibernate (built-in)',
         function='jsonrpc', kwargs_off=dict(method='System.Hibernate')),
    dict(name='quit-builtin', title='Quit (built-in)',
         function='jsonrpc', kwargs_off=dict(method='Application.Quit')),
    dict(name='shutdown-builtin', title='ShutDown action (built-in)',
         function='jsonrpc', kwargs_off=dict(method='System.Shutdown')),
    dict(name='reboot-builtin', title='Reboot (built-in)',
         function='jsonrpc', kwargs_off=dict(method='System.Reboot')),
    dict(name='powerdown-builtin', title='Powerdown (built-in)',
         function='jsonrpc', kwargs_off=dict(method='System.Powerdown')),
]


class SafeDict(dict):
    ''' A safe dictionary implementation that does not break down on missing keys '''
    def __missing__(self, key):
        ''' Replace missing keys with the original placeholder '''
        return '{' + key + '}'


def from_unicode(text, encoding='utf-8'):
    ''' Force unicode to text '''
    if sys.version_info.major == 2 and isinstance(text, unicode):  # noqa: F821; pylint: disable=undefined-variable
        return text.encode(encoding)
    return text


def to_unicode(text, encoding='utf-8'):
    ''' Force text to unicode '''
    return text.decode(encoding) if isinstance(text, bytes) else text


def log(level=1, msg='', **kwargs):
    ''' Log info messages to Kodi '''
    max_log_level = int(get_setting('max_log_level', 0))
    if not DEBUG_LOGGING and not (level <= max_log_level and max_log_level != 0):
        return
    from string import Formatter
    if kwargs:
        msg = Formatter().vformat(msg, (), SafeDict(**kwargs))
    msg = '[{addon}] {msg}'.format(addon=ADDON_ID, msg=msg)
    xlog(from_unicode(msg), level % 3 if DEBUG_LOGGING else 2)


def log_error(msg, **kwargs):
    ''' Log error messages to Kodi '''
    from string import Formatter
    if kwargs:
        msg = Formatter().vformat(msg, (), SafeDict(**kwargs))
    msg = '[{addon}] {msg}'.format(addon=ADDON_ID, msg=msg)
    xlog(from_unicode(msg), 4)


def jsonrpc(**kwargs):
    ''' Perform JSONRPC calls '''
    from json import dumps, loads
    if 'id' not in kwargs:
        kwargs.update(id=1)
    if 'jsonrpc' not in kwargs:
        kwargs.update(jsonrpc='2.0')
    result = loads(executeJSONRPC(dumps(kwargs)))
    log(3, msg="Sending JSON-RPC payload: '{payload}' returns '{result}'", payload=kwargs, result=result)
    return result


def get_setting(setting_id, default=None):
    ''' Get an add-on setting '''
    value = to_unicode(ADDON.getSetting(setting_id))
    if value == '' and default is not None:
        return default
    return value


def get_global_setting(setting):
    ''' Get a Kodi setting '''
    result = jsonrpc(method='Settings.GetSettingValue', params=dict(setting=setting))
    return result.get('result', {}).get('value')


def popup(heading='', msg='', delay=10000, icon=''):
    ''' Bring up a pop-up with a meaningful error '''
    if not heading:
        heading = 'Addon {addon} failed'.format(addon=ADDON_ID)
    if not icon:
        icon = ADDON_ICON
    Dialog().notification(heading, msg, icon, delay)


def set_mute(toggle=True):
    ''' Set mute using Kodi JSON-RPC interface '''
    jsonrpc(method='Application.SetMute', params=dict(mute=toggle))


def activate_window(window='home'):
    ''' Set mute using Kodi JSON-RPC interface '''
#    result = jsonrpc(method='GUI.ActivateWindow', params=dict(window=window, parameters=['Home']))
    jsonrpc(method='GUI.ActivateWindow', params=dict(window=window))


def run_builtin(builtin):
    ''' Run Kodi builtins while catching exceptions '''
    log(2, msg="Executing builtin '{builtin}'", builtin=builtin)
    executebuiltin(builtin, True)


def run_command(*command, **kwargs):
    ''' Run commands on the OS while catching exceptions '''
    # TODO: Add options for running using su or sudo
    try:
        cmd = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kwargs)
        (out, err) = cmd.communicate()
        if cmd.returncode == 0:
            log(2, msg="Running command '{command}' returned rc={rc}", command=' '.join(command), rc=cmd.returncode)
        else:
            log_error(msg="Running command '{command}' failed with rc={rc}", command=' '.join(command), rc=cmd.returncode)
            if err:
                log_error(msg="Command '{command}' returned on stderr: {stderr}", command=command[0], stderr=err)
            if out:
                log_error(msg="Command '{command}' returned on stdout: {stdout} ", command=command[0], stdout=out)
            popup(msg="%s\n%s" % (out, err))
            sys.exit(1)
    except OSError as exc:
        log_error(msg="Exception running '{command}': {exc}", command=command[0], exc=exc)
        popup(msg="Exception running '%s': %s" % (command[0], exc))
        sys.exit(2)


def func(function, *args, **kwargs):
    ''' Execute a global function with arguments '''
    return globals()[function](*args, **kwargs)


class TurnOffDialog(WindowXMLDialog, object):
    ''' The TurnOffScreensaver class managing the XML gui '''

    def __init__(self, *args):  # pylint: disable=super-init-not-called,unused-argument
        ''' Initialize dialog '''
        self.display = None
        self.logoff = None
        self.monitor = None
        self.mute = None
        self.power = None
        atexit.register(self.exit)

    def onInit(self):  # pylint: disable=invalid-name
        ''' Perform this when the screensaver is started '''
        self.logoff = get_setting('logoff', 'false')
        self.mute = get_setting('mute', 'true')

        display_method = int(get_setting('display_method', 0))
        self.display = DISPLAY_METHODS[display_method]

        power_method = int(get_setting('power_method', 0))
        self.power = POWER_METHODS[power_method]

        log(3, msg='display_method={display}, power_method={power}, logoff={logoff}, mute={mute}',
            display=self.display.get('name'), power=self.power.get('name'), logoff=self.logoff, mute=self.mute)

        # Turn off display
        if self.display.get('name') != 'do-nothing':
            log(1, msg="Turn display off using method '{name}'", **self.display)
        func(self.display.get('function'), *self.display.get('args_off'))

        # FIXME: Screensaver always seems to lock when started, requires unlock and re-login
        # Log off user
        if self.logoff == 'true':
            log(1, msg='Log off user')
#            run_builtin('System.LogOff')
            activate_window('loginscreen')
#            run_builtin('ActivateWindow(loginscreen)')
#            run_builtin('ActivateWindowAndFocus(loginscreen,return)')

        # Mute audio
        if self.mute == 'true':
            log(1, msg='Mute audio')
            set_mute(toggle=True)

        self.monitor = TurnOffMonitor(action=self.resume)

        # Power off system
        if self.power.get('name') != 'do-nothing':
            log(1, msg="Turn system off using method '{name}'", **self.power)
        func(self.power.get('function'), **self.power.get('kwargs_off', {}))

    def resume(self):
        ''' Perform this when the Screensaver is stopped '''
        # Unmute audio
        if self.mute == 'true':
            log(1, msg='Unmute audio')
            set_mute(toggle=False)

        # Turn on display
        if self.display.get('name') != 'do-nothing':
            log(1, msg="Turn display back on using method '{name}'", **self.display)
        func(self.display.get('function'), *self.display.get('args_on'))

        # Clean up everything
        self.exit()

    def exit(self):
        ''' Clean up function '''
        if hasattr(self, 'monitor'):
            del self.monitor

        self.close()
#        del self


class TurnOffMonitor(Monitor, object):
    ''' This is the monitor to exit TurnOffScreensaver '''

    def __init__(self, **kwargs):  # pylint: disable=super-init-not-called
        ''' Initialize monitor '''
        self.action = kwargs.get('action')

    def onScreensaverDeactivated(self):  # pylint: disable=invalid-name
        ''' Perform cleanup function '''
        self.action()


ADDON = Addon()
ADDON_NAME = to_unicode(ADDON.getAddonInfo('name'))
ADDON_ID = to_unicode(ADDON.getAddonInfo('id'))
ADDON_PATH = to_unicode(ADDON.getAddonInfo('path'))
ADDON_ICON = to_unicode(ADDON.getAddonInfo('icon'))

DEBUG_LOGGING = get_global_setting('debug.showloginfo')

if __name__ == '__main__':
    # Do not start screensaver when command fails
    TurnOffDialog('gui.xml', ADDON_PATH, 'default').doModal()
    sys.modules.clear()
