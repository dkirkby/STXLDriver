import time
import argparse
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


def take_exposure(camera, exptime, fname, shutter_open=True, timeout=10, fail_on_latchup=True):
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
    fail_on_latchup : bool
        When True, consider an exposure to have failed if a cooling latchup
        has been detected during the exposure.  Otherwise, read out the
        data and return normally.

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
    print('cooling', cooling, 'Tset', temperature_setpoint)
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
    if cooling and np.all(np.array(pwr_history) == 100) and np.min(temp_history) > temperature_setpoint + 2:
        print('Detected cooling latchup!')
        if fail_on_latchup:
            return False
    # Read the data from the camera.
    camera.save_exposure(fname)
    print('Saved {0}'.format(fname))
    return True


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='STXL calibration.')
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
    args = parser.parse_args()

    C = Camera(URL=args.url, verbose=False)
    initialize(C, args.binning, args.temperature)
    for i in range(args.nzero):
        ok = take_exposure(C, exptime=0., fname='data/zero_{0:03d}.fits'.format(i), shutter_open=False)
        if not ok:
            break
    for i in range(args.ndark):
        ok = take_exposure(C, exptime=args.tdark, fname='data/dark_{0:03d}.fits'.format(i), shutter_open=False)
        if not ok:
            break
