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


def main():
    parser = argparse.ArgumentParser(
        description='Stress test for STXL camera readout.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('-v', '--verbose', action='store_true',
        help='include DEBUG messages in the output log')
    parser.add_argument('--url', default='http://10.0.1.3',
        help='Camera interface URL to use')
    parser.add_argument('-t', '--exptime', type=float, default=5.,
        help='Exposure time in seconds to use')
    parser.add_argument('-b', '--binning', type=int, choices=(1, 2, 3), default=2,
        help='Camera pixel binning to use')
    parser.add_argument('-T', '--temperature', type=float, default=15.,
        help='Temperature setpoint to use in C')
    parser.add_argument('--outname', type=str, default='out.fits',
        help='Name of FITS file to write after each exposure')
    parser.add_argument('--log', type=str, default=None,
        help='Name of log file to write (default is stdout)')
    parser.add_argument('--ival', type=int, default=10,
        help='Logging interval in units of exposures')
    args = parser.parse_args()

    logging.basicConfig(filename=args.log, level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s', datefmt='%m/%d/%Y %H:%M:%S')
    logging.getLogger('urllib3').setLevel(logging.INFO)
    logging.getLogger('requests').setLevel(logging.WARNING)

    C = Camera(URL=args.url)
    init = lambda: C.initialize(binning=args.binning, temperature_setpoint=args.temperature)
    init()

    logging.info('Running until ^C or kill -SIGINT {0}'.format(os.getpgid(0)))
    nexp, nbad, last_nexp = 0, 0, 0
    start = time.time()
    try:
        while True:
            success = C.take_exposure(args.exptime, args.outname, latchup_action=init)
            nexp += 1
            if not success:
                nbad += 1
            if nexp % args.ival == 0:
                elapsed = time.time() - start
                deadtime = elapsed / (nexp - last_nexp) - args.exptime
                load = os.getloadavg()[1] # 5-min average number of processes in the system run queue.
                msg = ('nexp={0:05d}: nbad={1} deadtime {2:.1f}s/exp LOAD {3:.1f}'
                       .format(nexp, nbad, deadtime, load))
                logging.info(msg)
                # Reset statistics
                last_nexp = nexp
                start = time.time()
    except KeyboardInterrupt:
        logging.info('\nbye')
