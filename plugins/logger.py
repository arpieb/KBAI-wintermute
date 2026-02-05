from __future__ import print_function
from __future__ import unicode_literals

from rtmbot.core import Plugin

class LoggerPlugin(Plugin):

    def catch_all(self, data):
        if data['type'] != 'pong':
            print(data)

