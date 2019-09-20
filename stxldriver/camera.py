import datetime
import collections
import random
import time

import requests

import numpy as np

from .parse import IndexParser, FormParser, FilterParser


class Camera(object):

    def __init__(self, URL='http://10.0.1.3', verbose=True, timeout=60.):
        self.URL = URL
        if self.URL.endswith('/'):
            self.URL = self.URL[:-1]
        self.timeout = timeout
        self.read_info(verbose)
        self.methods = (
            'ImagerGetSettings.cgi?CCDTemperature',
            'ImagerGetSettings.cgi?CoolerPower',
            'CurrentCCDState.cgi',
            'CurrentCCDState.cgi?IncludeTimePct',
            'FilterState.cgi')
        self.setup = None
        self.network = None
        self.exposure_config = None
        self.filter_names = None

    def _display(self, D):
        width = np.max([len(name) for name in D.keys() if name[0] != '_'])
        fmt = '{{0:>{0}}} {{1}}'.format(width)
        for name, value in D.items():
            if name[0] == '_':
                continue
            print(fmt.format(name, value))

    def _get(self, path, stream=False, timeout=None):
        if timeout is None:
            # Use the default value.
            timeout = self.timeout
        try:
            response = requests.get(self.URL + path, timeout=timeout, stream=stream)
            response.raise_for_status()
            return response
        except requests.exceptions.RequestException as e:
            raise RuntimeError('Unable to get "{0}":\n{1}'.format(path, e))

    def read_info(self, verbose=True, timeout=None):
        response = self._get('/index.html', timeout=timeout)
        parser = IndexParser()
        parser.feed(response.text)
        self.properties = parser.properties
        if verbose:
            self._display(self.properties)

    def _read_form(self, path, name, verbose=True, timeout=None, return_response=False):
        response = self._get(path, timeout=timeout)
        parser = FormParser()
        parser.feed(response.text)
        form = parser.forms[name]
        if verbose:
            self._display(form)
        if return_response:
            return form, response
        else:
            return form

    def _build_query(self, defaults, kwargs):
        # Build the new setup to write.
        params = collections.OrderedDict(
            {name: value for name, value in defaults.items() if name[0] != '_'})
        for name, value in kwargs.items():
            if name not in params:
                raise ValueError('Invalid name: "{0}".'.format(name))
            params[name] = value
        # Build a URL query string with all parameters specified.
        queries = ['{0}={1}'.format(name, value) for name, value in params.items()]
        return params, '?' + '&'.join(queries)

    def reboot(self, verbose=True):
        if self.network is None:
            # Read the current network setup if necessary.
            self.network = self._read_form('/network.html', 'EthernetParams', verbose=False)
        # Submit the network form with no changes to trigger a reboot.
        _, query = self._build_query(self.network, {})
        try:
            # This request will normally time out instead of returning an updated
            # network page.
            self._read_form('/network.html' + query, 'EthernetParams', timeout=1)
        except RuntimeError as e:
            # This is expected. Wait 5s then try to load the info page and reset our state.
            time.sleep(5)
            self.read_info(verbose=verbose)

    def read_setup(self, query='', verbose=True):
        self.setup = self._read_form('/setup.html' + query, 'CameraSetup', verbose)

    def write_setup(self, max_retries=0, verbose=True, **kwargs):
        if self.setup is None:
            # Read the current setup if necessary.
            self.read_setup(verbose=False)
        # Build the new setup to write.
        new_setup, query = self._build_query(self.setup, kwargs)
        # Loop over attempts to write this setup.
        attempts = 0
        while attempts < 1 + max_retries:
            # Write the new setup.
            self.read_setup(query, verbose=False)
            attempts += 1
            # Check that the read back setup matches what we expect.
            verified = True
            for name, value in new_setup.items():
                read_value = type(value)(self.setup[name])
                if read_value != value:
                    verified = False
                    msg = 'wrote {0}={1} but read {2}.'.format(name, value, read_value)
                    if verbose:
                        print(msg)
            if verified:
                break
            # Re-read the current setup before retrying.
            self.read_setup(verbose=verbose)
            time.sleep(1)
        if not verified:
            raise RuntimeError('Failed to verify setup after {0} retries.'.format(max_retries))

    def init_filter_wheel(self, response=None, verbose=True):
        """Initialize the filter wheel.
        """
        self.read_filter_config(query='Filter=0', verbose=verbose)

    def read_filter_config(self, query='', verbose=True):
        self.filter_names, response = self._read_form(
            '/filtersetup.html' + query, 'FilterNames', verbose=False, return_response=True)
        parser = FilterParser()
        parser.feed(response.text)
        self.current_filter_number = parser.current_filter_number
        self.current_filter_name = self.filter_names['Filter{0}'.format(self.current_filter_number)]
        if verbose:
            print('Current filter is [{0}] {1}.'.format(
                self.current_filter_number, self.current_filter_name))

    def set_filter(self, filter_number, verbose=True, wait=True, max_wait=10):
        """Set the filter wheel position.
        """
        if filter_number not in (1, 2, 3, 4, 5, 6, 7, 8):
            raise ValueError('Invalid filter_number: {0}.'.format(filter_number))
        self.filter_names = self.read_filter_config(
            query='Filter={0}'.format(filter_number), verbose=verbose)
        if self.current_filter_number != filter_number:
            raise RuntimeError('Filter number mismatch: current={0} but requested={1}.'
                               .format(self.current_filter_number, filter_number))
        if wait:
            remaining = max_wait
            while remaining > 0:
                time.sleep(1)
                status = self.call_api('FilterState.cgi')
                if verbose:
                    print('Filter wheel status is {0} with {1}s remaining...'.format(status, remaining))
                if status[0] == '0': # Idle
                    break
                elif status[0] != '1' and status[1] != '': # Moving or unknown
                    raise RuntimeError(
                        'Failed to complete filter wheel move after {0} seconds.'.format(max_wait))
                remaining -= 1

    def read_exposure_config(self, query='', verbose=True):
        self.exposure_config = self._read_form('/exposure.html' + query, 'Exposure', verbose)

    def start_exposure(self, **kwargs):
        if self.exposure_config is None:
            self.read_exposure_config(verbose=False)
        # Save the current time formatted as a UTC ISO string.
        # Round to ms precision since the javascript code does this (but does it matter?)
        now = datetime.datetime.now()
        micros = round(now.microsecond, -3)
        if micros < 0:
            print('Got micros < 0:', micros)
            micros = 0
        if micros > 999000:
            print('Got micros > 999000:', micros)
            micros = 999000
        truncated = now.replace(microsecond = micros).isoformat()
        if truncated[-3:] != '000':
            print('DEBUG', now, micros, truncated)
        kwargs['DateTime'] = truncated[:-3]
        # Prepare the exposure parameters to use.
        new_exposure, query = self._build_query(self.exposure_config, kwargs)
        # Write the exposure parameters, which triggers the start.
        self._get('/exposure.html' + query)

    def save_exposure(self, filename, preview=False):
        path = '/Preview.jpg' if preview else '/Image.FIT'
        response = self._get(path, stream=True)
        with open(filename, 'wb') as fout:
            for chunk in response.iter_content(chunk_size=128):
                fout.write(chunk)

    def abort_exposure(self):
        self._get('/exposure.html?Abort')

    def call_api(self, method):
        if method not in self.methods:
            raise ValueError('Invalid method: choose one of {0}.'.format(",".join(self.methods)))
        # The random number added here follows the javascript GetDataFromURL in scripts.js
        r = random.random()
        url = self.URL + '/api/{0}&{1}'.format(method, r)
        try:
            response = requests.get(url, timeout=self.timeout)
            response.raise_for_status()
            return response.text.strip()
        except requests.exceptions.RequestException as e:
            raise RuntimeError('Unable to call API method "{0}":\n{1}'.format(method, e))
