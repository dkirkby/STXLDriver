"""Stress test driver for SkyCamera exposures.

Can be run in the background using, e.g.

  nohup python stxldriver/stress.py --url http://10.0.1.3 --exptime 5 &

To monitor progress:

  tail -f stress.log

Note that subsequent runs will append to an existing log, so delete it
first when you want to start a new log.
"""
import time
import argparse
import os
import logging

import numpy as np

from camera import Camera


def stress_test(camera, exptime, binning, temperature, interval=10, timeout=10):

    # Initialize the camera
    # CoolerState: 0=off, 1=on.
    # Fan: 1=auto, 2=manual, 3=disabled.
    logging.info('Initializing for {0}x{0} binning at {1}C...'.format(binning, temperature))
    camera.write_setup(Bin=binning, CCDTemperatureSetpoint=temperature, CoolerState=1, Fan=2, FanSetpoint=50)
    time.sleep(10)

    # Run until we get at SIGINT
    logging.info('Running until ^C or kill -SIGINT {0}'.format(os.getpgid(0)))
    nexp, last_nexp = 0, 0
    temp_history, pwr_history = [], []
    start = time.time()
    try:
        while True:
            # Start the next exposure.
            # ImageType: 0=dark, 1=light, 2=bias, 3=flat.
            # Contrast: 0=auto, 1=manual.
            camera.start_exposure(ExposureTime=exptime, ImageType=1, Contrast=1)
            # Monitor the temperature and cooler power during the exposure.
            cutoff = time.time() + exptime + timeout
            while time.time() < cutoff:
                # Read the current values.
                temp_history.append(float(camera.call_api('ImagerGetSettings.cgi?CCDTemperature')))
                pwr_history.append(float(camera.call_api('ImagerGetSettings.cgi?CoolerPower')))
                time.sleep(1.0)
                state = camera.call_api('CurrentCCDState.cgi')
                # Possible states are:
                # 0 : Idle
                # 2 : Exposing
                if state == '0':
                    break
            if state != '0':
                logging.warning('Found unexpected CCD state {0} after exposure {1}.'.format(state, nexp + 1))
            else:
                # Read the data from the camera, always using the same filename.
                camera.save_exposure('data/tmp.fits')
            nexp += 1
            if nexp % interval == 0:
                elapsed = time.time() - start
                deadtime = elapsed / (nexp - last_nexp) - exptime
                msg = ('nexp={0:05d}: dead {1:.1f}s, T {2:4.1f}/{3:4.1f}/{4:4.1f}C PWR {5:2.0f}/{6:2.0f}/{7:2.0f}%'
                       .format(nexp, deadtime, *np.percentile(temp_history, (0, 50, 100)),
                               *np.percentile(pwr_history, (0, 50, 100))))
                logging.info(msg)
                # Reset statistics
                last_nexp = nexp
                temp_history, pwr_history = [], []
                start = time.time()
    except KeyboardInterrupt:
        logging.info('\nbye')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='STXL stress test.')
    parser.add_argument('--url', default='http://10.0.1.3',
        help='Camera interface URL to use')
    parser.add_argument('-t', '--exptime', type=float, default=5.,
        help='Exposure time in seconds to use')
    parser.add_argument('-b', '--binning', type=int, choices=(1, 2, 3), default=2,
        help='Camera pixel binning to use')
    parser.add_argument('-T', '--temperature', type=float, default=15.,
        help='Temperature setpoint to use in C')
    parser.add_argument('--log', default='stress.log',
        help='Name of log file to write')
    parser.add_argument('--ival', type=int, default=10,
        help='Logging interval in units of exposures')
    parser.add_argument('--timeout', type=float, default=10,
        help='Maximum time allowed for camera readout in seconds')
    args = parser.parse_args()

    C = Camera(URL=args.url, verbose=False)
    logging.basicConfig(filename=args.log, level=logging.INFO, format='%(asctime)s %(message)s',
        datefmt='%m/%d/%Y %H:%M:%S')
    logging.getLogger('requests').setLevel(logging.WARNING)
    stress_test(C, args.exptime, args.binning, args.temperature, args.ival, args.timeout)
