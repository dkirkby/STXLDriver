"""Stress test driver for SkyCamera exposures.

Can be run in the background using, e.g.

  nohup python stress.py --url http://10.0.1.3 --exptime 5 &

To monitor progress:

  tail -f stress.log

Note that subsequent runs will append to an existing log, so delete it
first when you want to start a new log.
"""
import time
import argparse
import os
import sys
import logging

import numpy as np

from stxldriver.camera import Camera


def initialize(camera, binning, temperature):

    logging.info('Rebooting...')
    camera.reboot()
    time.sleep(30)

    # Initialize the camera
    # CoolerState: 0=off, 1=on.
    # Fan: 1=auto, 2=manual, 3=disabled.
    logging.info('Initializing for {0}x{0} binning at {1}C...'.format(binning, temperature))
    camera.write_setup(Bin=binning, CCDTemperatureSetpoint=temperature, CoolerState=1, Fan=2, FanSetpoint=50)
    time.sleep(15)


def stress_test(camera, exptime, binning, temperature, interval=10, timeout=10, processor=None):

    initialize(camera, binning, temperature)
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
            state = '?'
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
            if state != '0':
                logging.warning('Found unexpected CCD state {0} after exposure {1}.'.format(state, nexp + 1))
            else:
                # Read the data from the camera, always using the same filename.
                camera.save_exposure('data/tmp.fits')
                if processor is not None:
                    processor('data/tmp.fits')
            nexp += 1
            if nexp % interval == 0:
                elapsed = time.time() - start
                deadtime = elapsed / (nexp - last_nexp) - exptime
                load = os.getloadavg()[1] # 5-min average number of processes in the system run queue.
                msg = ('nexp={0:05d}: dead {1:.1f}s, T {2:4.1f}/{3:4.1f}/{4:4.1f}C PWR {5:2.0f}/{6:2.0f}/{7:2.0f}% LOAD {8:.1f}'
                       .format(nexp, deadtime, *np.percentile(temp_history, (0, 50, 100)),
                               *np.percentile(pwr_history, (0, 50, 100)), load))
                logging.info(msg)
                # Test for cooling latchup.
                if np.any(np.array(pwr_history) == 100) and np.min(temp_history) > temperature + 2:
                    logging.warning('Detected cooling latchup!')
                    initialize(camera, binning, temperature)
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
    parser.add_argument('--simulate', action='store_true',
        help='Add simulated fiber flux and run analysis')
    args = parser.parse_args()

    logging.basicConfig(filename=args.log, level=logging.INFO, format='%(asctime)s %(message)s',
        datefmt='%m/%d/%Y %H:%M:%S')
    logging.getLogger('requests').setLevel(logging.WARNING)

    if args.simulate:
        try:
            import desietcimg.sky
        except ImportError:
            print('The desietcimg package must be installed to use --simulate.')
            sys.exit(-1)
        import fitsio
        rng = np.random.RandomState()
        SCA = desietcimg.sky.SkyCameraAnalysis(binning=args.binning)
        SCA.load_fiber_locations()
        true_means = desietcimg.sky.init_signals(SCA.fibers, max_signal=5000., attenuation=0.95)
        simout = open('simulate.csv', 'w')
        logging.info('Simulation analysis results will be saved to simulate.csv')
        labels = [label for label in SCA.fibers]
        print(*labels, sep=',', file=simout)
        def processor(name):
            bg = fitsio.read(name)
            data, true_detected = desietcimg.sky.add_fiber_signals(bg, true_means, SCA, rng=rng)
            measured = SCA.get_fiber_fluxes(data)
            values = [measured[label][3] for label in labels]
            print(*values, sep=',', file=simout, flush=True)
    else:
        processor = None

    C = Camera(URL=args.url, verbose=False)
    stress_test(C, args.exptime, args.binning, args.temperature, args.ival, processor=processor)

    if args.simulate:
        simout.close()
