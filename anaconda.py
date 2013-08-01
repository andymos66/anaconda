# -*- coding: utf8 -*-

# Copyright (C) 2013 - Oscar Campos <oscar.campos@member.fsf.org>
# This program is Free Software see LICENSE file for details

"""
Anaconda is a python autocompletion and linting plugin for Sublime Text 3
"""

import os
import sys
import pipes
import socket
import logging
import threading
import subprocess

import sublime
import sublime_plugin

from anaconda.utils import get_settings
from anaconda.anaconda_client import Client
from anaconda.decorators import only_python, executor

if sys.version_info < (3, 3):
    raise RuntimeError('Anaconda only works with Sublime Text 3')

logger = logging.getLogger(__name__)
logger.addHandler(logging.StreamHandler(sys.stdout))
logger.setLevel(logging.ERROR)

WORKERS = {}
WORKERS_LOCK = threading.RLock()


###############################################################################
# Anaconda Plugin Subclasses
###############################################################################
# class AnacondaParamsAutocomplete(sublime_plugin.TextCommand):
#     """Autocomplete commands using JEDI
#     """

#     @only_python
#     def run(self, edit):
#         """
#         """

#         pass


class AnacondaCompletionsListener(sublime_plugin.EventListener):
    """Jedi autocompletion for Sublime Text
    """

    completions = []

    @only_python
    def on_query_completions(self, view, prefix, locations):
        """Sublime Text autocompletion event handler
        """

        logger.info('Anaconda completion has been called')
        proposals = Worker.lookup(view).autocomplete(locations[0])

        if proposals:
            completion_flags = (
                sublime.INHIBIT_WORD_COMPLETIONS |
                sublime.INHIBIT_EXPLICIT_COMPLETIONS
            )
            return (proposals, completion_flags)

        return proposals


###############################################################################
# Classes
###############################################################################
class Worker:
    """Worker class for subprocess manipulation
    """

    def __init__(self, view):
        self.view = view
        self.client = None
        self.proccess = None
        self.restart()

    def restart(self):
        """Restart the server
        """

        self.proccess = self.start_worker(self)
        logger.info('starting anaconda server on port {}'.format(self.port))
        self.client = Client('localhost', self.port)

    def stop(self):
        """Stop any configured server for this Worker
        """

        if self.client is not None:
            self.client.close()
            self.client = None

        if self.proccess is not None:
            self.proccess.terminate()
            self.process = None

    @executor
    def autocomplete(self, location):
        """Call to autocomplete in the client
        """

        current_line, current_column = self.view.rowcol(location)
        data = {
            'source': self.view.substr(sublime.Region(0, self.view.size())),
            'line': current_line + 1,
            'offset': current_column,
            'filename': self.view.file_name() or ''
        }

        result = self.client.request('autocomplete', **data)
        if result and result['success'] is True:
            return result['completions']

    @executor
    def run_linter(self, text, settings, filename):
        """Run the Linters in the client
        """

        data = {'code': text, 'settings': settings, 'filename': filename}
        result = self.client.request('run_linter', **data)
        if result and result['success'] is True:
            return result['errors']

    @staticmethod
    def port():
        """Get a free port
        """

        s = socket.socket()
        s.bind(('', 0))
        port = s.getsockname()[1]
        s.close()

        return port

    @staticmethod
    def lookup(view):
        """Lookup a Worker in the Workers stack
        """

        window = view.window()
        if window.window_id not in WORKERS:
            with WORKERS_LOCK:
                WORKERS[window.window_id] = Worker(view)

        return WORKERS[window.window_id]

    @staticmethod
    def generate_project_name(window):
        """
        Generates and returns back a valid project name for the window

        If there is not worker yet for this window, we create it and set a
        name for it. If we don't have a project file we just use the first
        folder name in the window's folders as name, if we don't have any
        folders in the window we just use the window.window_id
        """
        project_name = window.project_file_name()
        if project_name is None:
            folders = window.folders()
            if len(folders) > 0:
                project_name = window.folders()[0].rsplit('/', 1)[1]
            else:
                project_name = 'anaconda-{id}'.format(id=window.window_id)
        else:
            project_name = project_name.rsplit('/', 1)[1].split('.')[0]

        return project_name

    @staticmethod
    def start_worker(self):
        """Start a worker subprocess
        """

        self.port = Worker.port()
        window = self.view.window()
        project_name = Worker.generate_project_name(window)

        interp = get_settings(
            window.active_view(), 'python_interpreter', default='python'
        )
        extra_paths = get_settings(
            window.active_view(), 'extra_paths', default=''
        )

        kwargs = {
            'cwd': os.path.dirname(os.path.abspath(__file__)),
            'bufsize': -1
        }

        if sublime.platform() == 'windows':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            kwargs['startupinfo'] = startupinfo

        WORKER_SCRIPT = pipes.quote(
            os.path.join(
                os.path.dirname(__file__), 'anaconda_server/jsonserver.py')
        )
        sub_args = [
            interp, '-B', WORKER_SCRIPT, '-p', project_name, str(self.port)
        ]
        for extra_path in extra_paths.split(','):
            if extra_path != '':
                sub_args.extend(['-e', extra_path])
        sub_args.extend([str(os.getpid())])

        return subprocess.Popen(sub_args, **kwargs)