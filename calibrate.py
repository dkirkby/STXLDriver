import time
import argparse
import numpy as np

from stxldriver.camera import Camera


def initialize(camera, binning, temperature, min_history=10):
    print('Rebooting...')
    camera.reboot()
    time.sleep(30)
    camera.write_setup(Bin=binning, CCDTemperatureSetpoint=temperature, CoolerState=1, Fan=2, FanSetpoint=50)
    time.sleep(15)
    print('Waiting for cooldown to {0:.1f}C...'.format(temperature))
    history = []
    while True:
        time.sleep(1)
        history.append(float(camera.call_api('ImagerGetSettings.cgi?CCDTemperature')))
        tavg = np.mean(history[-min_history:])
        print('  T={0:.3f}, Tavg={1:.3f}'.format(history[-1], tavg))
        if len(history) >= min_history and np.abs(tavg - temperature) < 0.05:
            break


def take_exposure(camera, exptime, fname, temperature, timeout=10):
    camera.start_exposure(ExposureTime=exptime, ImageType=0, Contrast=1)
    # Monitor the temperature and cooler power during the exposure.
    cutoff = time.time() + exptime + timeout
    state = '?'
    temp_history, pwr_history = [], []
    while time.time() < cutoff:
        # Read the current state, but keep going in case of a network problem.
        try:
            temp_now = float(camera.call_api('ImagerGetSettings.cgi?CCDTemperature'))
            pwr_now = float(camera.call_api('ImagerGetSettings.cgi?CoolerPower'))
            state = camera.call_api('CurrentCCDState.cgi')
            temp_history.append(temp_now)
            pwr_history.append(pwr_now)
            # State: 0=Idle, 2=Exposing
            if state == '0':
                break
        except RuntimeError as e:
            logging.warning(e)
        time.sleep(1.0)
    msg = ('T {0:4.1f}/{1:4.1f}/{2:4.1f}C PWR {3:2.0f}/{4:2.0f}/{5:2.0f}%'
            .format(*np.percentile(temp_history, (0, 50, 100)),
                    *np.percentile(pwr_history, (0, 50, 100))))
    print(msg)
    if state != '0':
        logging.warning('Found unexpected CCD state {0} for {1}.'.format(state, fname))
    else:
        if np.all(np.array(pwr_history) == 100) and np.min(temp_history) > temperature + 2:
            print('Detected cooling latchup!')
        # Read the data from the camera, always using the same filename.
        camera.save_exposure(fname)
        print('Saved {0}'.format(fname))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='STXL calibration.')
    parser.add_argument('--url', default='http://10.0.1.3',
        help='Camera interface URL to use')
    parser.add_argument('-b', '--binning', type=int, choices=(1, 2, 3), default=1,
        help='Camera pixel binning to use')
    parser.add_argument('-T', '--temperature', type=float, default=15.,
        help='Temperature setpoint to use in C')
    parser.add_argument('--nzero', type=int, default=10,
        help='Number of zero-length exposures to take')
    args = parser.parse_args()

    C = Camera(URL=args.url, verbose=False)
    initialize(C, args.binning, args.temperature)
    for i in range(args.nzero):
        take_exposure(camera, 0., 'data/zero_{0:03d}.fits'.format(i), args.temperature)
