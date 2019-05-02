import time
import numpy as np

from camera import Camera


def calibrate_dark(
    camera, temperature=10.,
    exptimes=[0., 1., 2., 4., 8., 16., 32., 64.], ncycles=5, min_history=10):
    """Perform a sequence of dark calibration exposures.
    """
    ttotal = ncycles + np.sum(exptimes) + min_history
    print(f'Estimated time: {ttotal:.1f}s')
    # Cool the camera down.
    camera.write_setup(Bin=1, CCDTemperatureSetpoint=temperature, CoolerState=1)
    print(f'Waiting for cooldown to {temperature:.1f}C...')
    history = []
    while True:
        time.sleep(1)
        history.append(float(camera.call_api('ImagerGetSettings.cgi?CCDTemperature')))
        tavg = np.mean(history[-min_history:])
        print(f'  T={history[-1]:.3f}, Tavg={tavg:.3f}')
        if len(history) >= min_history and np.abs(tavg - temperature) < 0.05:
            break
    # Loop over cycles.
    for cycle in range(ncycles):
        print('Starting cycle {cycle + 1} / {ncycles}...')
        # Loop over exposure times.
        for exptime in exptimes:
            print(f'   Starting {exptime:.0f}s exposure...')
            camera.start_exposure(ExposureTime=exptime, ImageType=0)
            now = time.time()
            end = now + exptime + 5
            while now < end:
                time.sleep(1.)
                Tnow = float(camera.call_api('ImagerGetSettings.cgi?CCDTemperature'))
                if np.abs(Tnow - temperature) > 0.1:
                    print(f'  * temperature is not stable (now {Tnow:.3f}C).')
                state = camera.call_api('CurrentCCDState.cgi')
                if state == '3':
                    break
            if state != '3':
                print(f'  *** Found unexpected CCD state after exposure: {state}.')
            else:
                fname = f'data/calib_{temperature:.1f}_{exptime:.1f}_{cycle}.fits'
                print(f'   Writing {fname}...')
            camera.save_exposure(fname)


if __name__ == '__main__':
    C = Camera()
    calibrate_dark(C)
