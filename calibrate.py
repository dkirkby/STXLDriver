import time
import argparse
import os.path
import sys
import glob
import re

import numpy as np

from stxldriver.camera import Camera


def initialize(camera, binning=2, reboot=True, fan_setpoint=50, temperature_setpoint=15,
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
        print('Rebooting...')
        camera.reboot()
    if fan_setpoint is None:
        # The fan speed is set automatically.
        camera.write_setup(Fan=1)
    else:
        if fan_setpoint < 0 or fan_setpoint > 100:
            raise ValueError('Invalid fan_setpoint {0}%. Must be 0-100.'.format(fan_setpoint))
        # For some reason, several retries are sometimes necesary to change the fan setup.
        try:
            # Set 100% then the desired value to provide some audible feedback.
            camera.write_setup(Fan=2, FanSetpoint=100.0)
            time.sleep(2)
            camera.write_setup(Fan=2, FanSetpoint=float(fan_setpoint))
        except RuntimeError as e:
            # This sometimes happens but we keep going when it does.
            pass
    if temperature_setpoint is None:
        camera.write_setup(CoolerState=0)
    else:
        if temperature_setpoint < 0 or temperature_setpoint > 30:
            raise ValueError('Invalid temperature_setpoint {0}C. Must be 0-30.',format(temperature_setpoint))
        camera.write_setup(CCDTemperatureSetpoint=float(temperature_setpoint), CoolerState=1)
    camera.write_setup(Bin=binning)
    print('Waiting for cooldown to {0:.1f}C...'.format(temperature_setpoint))
    history = []
    while True:
        time.sleep(1)
        history.append(float(camera.call_api('ImagerGetSettings.cgi?CCDTemperature')))
        tavg = np.mean(history[-num_temperature_samples:])
        print('  T={0:.3f}, Tavg={1:.3f}'.format(history[-1], tavg))
        if len(history) < num_temperature_samples:
            # Wait to accumulate more samples.
            continue
        if np.abs(tavg - temperature_setpoint) < max_tavg_error:
            # Average temperature is close enough to the setpoint.
            break
        if low_temperature_ok and tavg < temperature_setpoint:
            # Average temperature is below the setpoint and this is ok.
            break


def take_exposure(camera, exptime, fname, shutter_open=True, timeout=10, latchup_action=None):
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
    if camera.setup is None:
        raise RuntimeError('Camera has not been initialized.')
    cooling = int(camera.setup['CoolerState']) == 1
    temperature_setpoint = float(camera.setup['CCDTemperatureSetpoint'])
    # Start the exposure.
    ImageType = 1 if shutter_open else 0
    camera.start_exposure(ExposureTime=float(exptime), ImageType=ImageType, Contrast=1)
    # Monitor the temperature and cooler power during the exposure.
    cutoff = time.time() + exptime + timeout
    state = '?'
    temp_history, pwr_history = [], []
    while time.time() < cutoff:
        # Read the current state, but keep going in case of a network problem.
        try:
            if cooling:
                temp_now = float(camera.call_api('ImagerGetSettings.cgi?CCDTemperature'))
                pwr_now = float(camera.call_api('ImagerGetSettings.cgi?CoolerPower'))
                temp_history.append(temp_now)
                pwr_history.append(pwr_now)
            # State: 0=Idle, 2=Exposing
            state = camera.call_api('CurrentCCDState.cgi')
            if state == '0':
                break
        except RuntimeError as e:
            print('Unable to read current state:\n{0}'.format(e))
        time.sleep(1.0)
    if cooling:
        msg = ('T {0:4.1f}/{1:4.1f}/{2:4.1f}C PWR {3:2.0f}/{4:2.0f}/{5:2.0f}%'
            .format(*np.percentile(temp_history, (0, 50, 100)),
                    *np.percentile(pwr_history, (0, 50, 100))))
        print(msg)
    if state != '0':
        print('Found unexpected CCD state {0} for {1}.'.format(state, fname))
        return False
    if cooling and np.any(np.array(pwr_history) == 100) and np.min(temp_history) > temperature_setpoint + 2:
        print('Detected cooling latchup!')
        if latchup_action is not None:
            latchup_action()
            return False
    # Read the data from the camera.
    camera.save_exposure(fname)
    print('Saved {0}'.format(fname))
    return True


def next_index(pattern, verbose=True):
    found = sorted(glob.glob(pattern.format(N='*')))
    if found:
        regexp = re.compile(pattern.format(N='([0-9]+)'))
        nextidx = int(re.match(regexp, found[-1]).group(1)) + 1
        if verbose:
            print('Found {0} files matching "{1}". Next index is {2}.'
                  .format(len(found), pattern, nextidx))
        return nextidx
    else:
        return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='STXL calibration.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('-v', '--verbose', action='store_true',
        help='be verbose about progress')
    parser.add_argument('--url', default='http://10.0.1.3',
        help='camera interface URL to use')
    parser.add_argument('-b', '--binning', type=int, choices=(1, 2, 3), default=1,
        help='camera pixel binning to use (1,2 or 3)')
    parser.add_argument('-T', '--temperature', type=float, default=15.,
        help='temperature setpoint to use in C')
    parser.add_argument('--nzero', type=int, default=0, metavar='N',
        help='number of zero-length exposures to take')
    parser.add_argument('--ndark', type=int, default=0, metavar='N',
        help='number of dark (shutter closed) exposures to take')
    parser.add_argument('--tdark', type=float, default=120, metavar='SECONDS',
        help='dark exposure length in seconds')
    parser.add_argument('--nflat', type=int, default=0, metavar='N',
        help='number of flat (shutter open) exposures to take')
    parser.add_argument('--tflat', type=float, default=1, metavar='SECONDS',
        help='flat exposure length in seconds')
    parser.add_argument('--outpath', type=str, metavar='PATH', default='.',
        help='existing path where output file are written')
    parser.add_argument('--zero-name', type=str, metavar='NAME', default='zero_{N}.fits',
        help='format string for zero file names using {N} for sequence number')
    parser.add_argument('--dark-name', type=str, metavar='NAME', default='dark_{N}.fits',
        help='format string for dark file names using {N} for sequence number')
    parser.add_argument('--flat-name', type=str, metavar='NAME', default='flat_{N}.fits',
        help='format string for dark file names using {N} for sequence number')
    args = parser.parse_args()

    outpath = os.path.abspath(args.outpath)
    if not os.path.exists(outpath):
        print('Non-existant output path: {0}'.format(args.outpath))
        sys.exit(-1)

    C = Camera(URL=args.url, verbose=False)
    init = lambda: initialize(C, binning=args.binning, temperature_setpoint=args.temperature)
    init()

    zero_name = os.path.join(outpath, args.zero_name)
    i = i0 = next_index(zero_name, verbose=args.verbose)
    fname_format = zero_name.format(N='{N:03d}')
    while i < i0 + args.nzero:
        fname = os.path.join(outpath, fname_format.format(N=i))
        if take_exposure(C, exptime=0., fname=fname, shutter_open=False, latchup_action=init):
            i += 1

    dark_name = os.path.join(outpath, args.dark_name)
    i = i0 = next_index(dark_name, verbose=args.verbose)
    fname_format = dark_name.format(N='{N:03d}')
    while i < i0 + args.ndark:
        fname = os.path.join(outpath, fname_format.format(N=i))
        if take_exposure(C, exptime=args.tdark, fname=fname, shutter_open=False, latchup_action=init):
            i += 1

    flat_name = os.path.join(outpath, args.flat_name)
    i = i0 = next_index(flat_name, verbose=args.verbose)
    fname_format = flat_name.format(N='{N:03d}')
    while i < i0 + args.nflat:
        fname = os.path.join(outpath, fname_format.format(N=i))
        if take_exposure(C, exptime=args.tflat, fname=fname, shutter_open=True, latchup_action=init):
            i += 1
