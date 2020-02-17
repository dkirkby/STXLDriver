import datetime
import collections
import random
import time
import logging

import requests

import numpy as np

from .parse import IndexParser, FormParser, FilterParser


class Camera(object):
    """Driver for a single STXL camera identified by its URL.

    After creating a camera object, use the initialize() and
    take_exposure() methods for a high-level API.
    """
    def __init__(self, URL='http://10.0.1.3', timeout=60.):
        self.URL = URL
        if self.URL.endswith('/'):
            self.URL = self.URL[:-1]
        self.timeout = timeout
        self.read_info()
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
            logging.info(fmt.format(name, value))

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

    def read_info(self, timeout=None):
        response = self._get('/index.html', timeout=timeout)
        parser = IndexParser()
        parser.feed(response.text)
        self.properties = parser.properties
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

    def reboot(self):
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
            self.read_info()

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
                    logging.info(msg)
            if verified:
                break
            # Re-read the current setup before retrying.
            self.read_setup(verbose=verbose)
            time.sleep(1)
        if not verified:
            raise RuntimeError('Failed to verify setup after {0} retries.'.format(max_retries))

    def init_filter_wheel(self):
        """Initialize the filter wheel.
        """
        self.read_filter_config(query='Filter=0')

    def read_filter_config(self, query=''):
        self.filter_names, response = self._read_form(
            '/filtersetup.html' + query, 'FilterNames', verbose=False, return_response=True)
        parser = FilterParser()
        parser.feed(response.text)
        self.current_filter_number = parser.current_filter_number
        self.current_filter_name = self.filter_names['Filter{0}'.format(self.current_filter_number)]
        logging.info('Current filter is [{0}] {1}.'.format(
                     self.current_filter_number, self.current_filter_name))

    def set_filter(self, filter_number, wait=True, max_wait=10):
        """Set the filter wheel position.
        """
        if filter_number not in (1, 2, 3, 4, 5, 6, 7, 8):
            raise ValueError('Invalid filter_number: {0}.'.format(filter_number))
        self.filter_names = self.read_filter_config(query='Filter={0}'.format(filter_number))
        if self.current_filter_number != filter_number:
            raise RuntimeError('Filter number mismatch: current={0} but requested={1}.'
                               .format(self.current_filter_number, filter_number))
        if wait:
            remaining = max_wait
            while remaining > 0:
                time.sleep(1)
                status = self.call_api('FilterState.cgi')
                logging.debug('Filter wheel status is {0} with {1}s remaining...'.format(status, remaining))
                if status[0] == '0': # Idle
                    break
                elif status[0] != '1' and status[1] != '': # Moving or unknown
                    raise RuntimeError(
                        'Failed to complete filter wheel move after {0} seconds.'.format(max_wait))
                remaining -= 1

    def read_exposure_config(self, query='', verbose=True):
        self.exposure_config = self._read_form('/exposure.html' + query, 'Exposure', verbose)

    def start_exposure(self, **kwargs):
        """
        ImageType: 0=dark, 1=light, 2=bias, 3=flat.
        Contrast: 0=auto, 1=manual.
        """
        if self.exposure_config is None:
            self.read_exposure_config(verbose=False)
        # Save the current time formatted as a UTC ISO string.
        # Round to ms precision since the javascript code does this (but does it matter?)
        now = datetime.datetime.now()
        micros = round(now.microsecond, -3)
        if micros < 0:
            logging.warning('Got micros < 0:', micros)
            micros = 0
        if micros > 999000:
            logging.warning('Got micros > 999000: {0}'.format(micros))
            micros = 999000
        truncated = now.replace(microsecond = micros).isoformat()
        if truncated[-3:] != '000':
            logging.warning('truncated[-3:] != 000: {0}, {1}, {2}'.format(now, micros, truncated))
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

    def initialize(self, binning=2, reboot=True, fan_setpoint=50, temperature_setpoint=15,
                num_temperature_samples=10, max_tavg_error=0.05, low_temperature_ok=False):
        """Initialize the camera for data taking.

        Must be called before :meth:`take_exposure`.

        The time required to initialize will depend on whether a `reboot` is requested
        and how the cooling is configured.

        Parameters
        ----------
        binning : 1, 2 or 3
            The readout binning factor to use.
        reboot : bool
            Reboot the camera before initializing when True.
        fan_setpoint : float or None
            Use the specified percentage (0-100) fan speed, or allow the fan speed to be
            set automatically when None.
        temperature_setpoint : float or None
            Operate at the specified temperature (0-30) in degC, or disable active cooling
            when None.  When a setpoint is specified, this method will wait until it has
            been reached before returning.  The remaining parameters determine exactly how
            this is implemented.
        num_temperature_samples : int
            The current temperature is an average of this many samples taken at 1Hz.
        max_tavg_error : float
            The average used to estimate the current temperature must be within this range
            of the setpoint (in degC) in order to consider the camera to have reached its
            setpoint.
        low_temperature_ok : bool
            The camera is considered initialized if its average temperature is below the
            setpoint when True.  This is needed when the setpoint is above the ambient
            temperature, but means that the camera is operating only with an upper
            bound on its temperature.
        """
        if reboot:
            logging.warning('Rebooting...')
            self.reboot()
        if fan_setpoint is None:
            # The fan speed is set automatically.
            self.write_setup(Fan=1)
        else:
            if fan_setpoint < 0 or fan_setpoint > 100:
                raise ValueError('Invalid fan_setpoint {0}%. Must be 0-100.'.format(fan_setpoint))
            # For some reason, several retries are sometimes necesary to change the fan setup.
            try:
                # Set 100% then the desired value to provide some audible feedback.
                self.write_setup(Fan=2, FanSetpoint=100.0)
                time.sleep(2)
                self.write_setup(Fan=2, FanSetpoint=float(fan_setpoint))
            except RuntimeError as e:
                # This sometimes happens but we keep going when it does.
                pass
        self.write_setup(Bin=binning)
        if temperature_setpoint is None:
            self.write_setup(CoolerState=0)
            return
        # Temperature regulation requested
        if temperature_setpoint < 0 or temperature_setpoint > 30:
            raise ValueError('Invalid temperature_setpoint {0}C. Must be 0-30.',format(temperature_setpoint))
        self.write_setup(CCDTemperatureSetpoint=float(temperature_setpoint), CoolerState=1)
        logging.info('Waiting for cooldown to {0:.1f}C...'.format(temperature_setpoint))
        history = []
        while True:
            time.sleep(1)
            history.append(float(self.call_api('ImagerGetSettings.cgi?CCDTemperature')))
            tavg = np.mean(history[-num_temperature_samples:])
            logging.debug('Cooling down: T={0:.3f}, Tavg={1:.3f}'.format(history[-1], tavg))
            if len(history) < num_temperature_samples:
                # Wait to accumulate more samples.
                continue
            if np.abs(tavg - temperature_setpoint) < max_tavg_error:
                # Average temperature is close enough to the setpoint.
                break
            if low_temperature_ok and tavg < temperature_setpoint:
                # Average temperature is below the setpoint and this is ok.
                break

    def take_exposure(self, exptime, fname, shutter_open=True, timeout=10, latchup_action=None):
        """Take one exposure.

        The camera must be initialized first.

        Parameters
        ----------
        exptime : float
            The exposure time in seconds to use. Can be zero.
        fname : str
            The name of the FITS file where a successful exposure will be saved.
        shutter_open : bool
            When True, the shutter will be open during the exposure.
        timeout : float
            If the camera state has not changed to Idle after exptime + timeout,
            assume there is a problem. Value is in seconds.
        latchup_action : callable or None
            Function to call if a cooling latchup condition is detected or None.
            When None, a latchup is ignored. Otherwise this function is called
            and we return False without saving the data.

        Returns
        -------
        bool
            True when a FITS file was successfully written.
        """
        # Lookup the current temperature setpoint.
        if self.setup is None:
            raise RuntimeError('Camera has not been initialized.')
        cooling = int(self.setup['CoolerState']) == 1
        temperature_setpoint = float(self.setup['CCDTemperatureSetpoint'])
        # Start the exposure.
        ImageType = 1 if shutter_open else 0
        self.start_exposure(ExposureTime=float(exptime), ImageType=ImageType, Contrast=1)
        # Monitor the temperature and cooler power during the exposure.
        cutoff = time.time() + exptime + timeout
        state = '?'
        temp_history, pwr_history = [], []
        while time.time() < cutoff:
            # Read the current state, but keep going in case of a network problem.
            try:
                if cooling:
                    temp_now = float(self.call_api('ImagerGetSettings.cgi?CCDTemperature'))
                    pwr_now = float(self.call_api('ImagerGetSettings.cgi?CoolerPower'))
                    temp_history.append(temp_now)
                    pwr_history.append(pwr_now)
                # State: 0=Idle, 2=Exposing
                state = self.call_api('CurrentCCDState.cgi')
                if state == '0':
                    break
            except RuntimeError as e:
                logging.warning('Unable to read current state:\n{0}'.format(e))
            time.sleep(1.0)
        if cooling:
            msg = ('T {0:4.1f}/{1:4.1f}/{2:4.1f}C PWR {3:2.0f}/{4:2.0f}/{5:2.0f}%'
                .format(*np.percentile(temp_history, (0, 50, 100)),
                        *np.percentile(pwr_history, (0, 50, 100))))
            logging.debug(msg)
        if state != '0':
            logging.warning('Found unexpected CCD state {0} for {1}.'.format(state, fname))
            return False
        if cooling and np.any(np.array(pwr_history) == 100) and np.min(temp_history) > temperature_setpoint + 2:
            logging.warning('Detected cooling latchup!')
            if latchup_action is not None:
                latchup_action()
                return False
        # Read the data from the camera.
        self.save_exposure(fname)
        logging.debug('Saved {0}'.format(fname))
        return True
