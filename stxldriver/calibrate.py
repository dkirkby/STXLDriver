import time
import argparse
import numpy as np

from camera import Camera


def calibrate_dark(
    camera, temperature=10.,
    exptimes=[0., 1., 2., 4., 8., 16., 32., 64.], ncycles=5, min_history=10):
    """Perform a sequence of dark calibration exposures.
    """
    ttotal = ncycles + np.sum(exptimes) + min_history
    print('Estimated time: {0:.1f}s'.format(ttotal))
    # Cool the camera down.
    camera.write_setup(Bin=1, CCDTemperatureSetpoint=temperature, CoolerState=1)
    print('Waiting for cooldown to {0:.1f}C...'.format(temperature))
    history = []
    while True:
        time.sleep(1)
        history.append(float(camera.call_api('ImagerGetSettings.cgi?CCDTemperature')))
        tavg = np.mean(history[-min_history:])
        print('  T={0:.3f}, Tavg={1:.3f}'.format(history[-1], tavg))
        if len(history) >= min_history and np.abs(tavg - temperature) < 0.05:
            break
    # Loop over cycles.
    for cycle in range(ncycles):
        print('Starting cycle {0} / {1}...'.format(cycle + 1, ncycles))
        # Loop over exposure times.
        for exptime in exptimes:
            print('   Starting {0:.0f}s exposure...'.format(exptime))
            camera.start_exposure(ExposureTime=exptime, ImageType=0)
            now = time.time()
            end = now + exptime + 5
            while now < end:
                time.sleep(1.)
                Tnow = float(camera.call_api('ImagerGetSettings.cgi?CCDTemperature'))
                if np.abs(Tnow - temperature) > 0.1:
                    print('  * temperature is not stable (now {0:.3f}C).'.format(Tnow))
                state = camera.call_api('CurrentCCDState.cgi')
                if state == '3':
                    break
            if state != '3':
                print('  *** Found unexpected CCD state after exposure: {0}.'.format(state))
            else:
                fname = 'data/calib_{0:.1f}_{1:.1f}_{2}.fits'.format(temperature, exptime, cycle)
                print('   Writing {0}...'.format(fname))
            camera.save_exposure(fname)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='STXL calibration.')
    parser.add_argument('-T', '--temperature', type=float, default=15.,
        help='Temperature setpoint to use in C')
    parser.add_argument('-n', '--ncycles', type=int, default=5,
        help='Number of calibration cycles to perform')
    args = parser.parse_args()
    C = Camera()
    calibrate_dark(C, temperature=args.temperature, ncycles=args.ncycles)
