import time
import argparse
import os.path
import sys
import glob
import re
import logging


import numpy as np

from stxldriver.camera import Camera


def next_index(pattern):
    found = sorted(glob.glob(pattern.format(N='*')))
    if found:
        regexp = re.compile(pattern.format(N='([0-9]+)'))
        nextidx = int(re.match(regexp, found[-1]).group(1)) + 1
        logging.info('Found {0} files matching "{1}". Next index is {2}.'
                        .format(len(found), pattern, nextidx))
        return nextidx
    else:
        return 0


def main():
    parser = argparse.ArgumentParser(
        description='Collect calibration data from an STXL camera.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
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
    parser.add_argument('--tdark', type=float, default=120, metavar='T',
        help='dark exposure length in seconds')
    parser.add_argument('--nflat', type=int, default=0, metavar='N',
        help='number of flat (shutter open) exposures to take')
    parser.add_argument('--tflat', type=str, default='0.5,1.0,1.5,2.0', metavar='T1,T2,...',
        help='flat exposure lengths in seconds to cycle through')
    parser.add_argument('--outpath', type=str, metavar='PATH', default='.',
        help='existing path where output file are written')
    parser.add_argument('--zero-name', type=str, metavar='NAME', default='zero_{N}.fits',
        help='format string for zero file names using {N} for sequence number')
    parser.add_argument('--dark-name', type=str, metavar='NAME', default='dark_{N}.fits',
        help='format string for dark file names using {N} for sequence number')
    parser.add_argument('--flat-name', type=str, metavar='NAME', default='flat_{N}.fits',
        help='format string for dark file names using {N} for sequence number')
    parser.add_argument('--log', type=str, metavar='LOG', default=None,
        help='Name of log file to write (default is stdout)')
    args = parser.parse_args()

    logging.basicConfig(filename=args.log, level=logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s', datefmt='%m/%d/%Y %H:%M:%S')
    logging.getLogger('requests').setLevel(logging.WARNING)

    outpath = os.path.abspath(args.outpath)
    if not os.path.exists(outpath):
        logging.error('Non-existant output path: {0}'.format(args.outpath))
        sys.exit(-1)

    try:
        tflat = [float(T) for T in args.tflat.split(',')]
    except ValueError:
        logging.error('Invalid tflat: {0}'.format(args.tflat))
        sys.exit(-1)
    if args.nflat > 0 and args.nflat % len(tflat) > 0:
        logging.warning('nflat does not evenly divide number of flat exposure times.')

    C = Camera(URL=args.url, verbose=False)
    init = lambda: C.initialize(binning=args.binning, temperature_setpoint=args.temperature)
    init()

    zero_name = os.path.join(outpath, args.zero_name)
    i = i0 = next_index(zero_name)
    fname_format = zero_name.format(N='{N:03d}')
    while i < i0 + args.nzero:
        fname = os.path.join(outpath, fname_format.format(N=i))
        if C.take_exposure(exptime=0., fname=fname, shutter_open=False, latchup_action=init):
            i += 1

    dark_name = os.path.join(outpath, args.dark_name)
    i = i0 = next_index(dark_name)
    fname_format = dark_name.format(N='{N:03d}')
    while i < i0 + args.ndark:
        fname = os.path.join(outpath, fname_format.format(N=i))
        if C.take_exposure(exptime=args.tdark, fname=fname, shutter_open=False, latchup_action=init):
            i += 1

    flat_name = os.path.join(outpath, args.flat_name)
    i = i0 = next_index(flat_name)
    fname_format = flat_name.format(N='{N:03d}')
    while i < i0 + args.nflat:
        fname = os.path.join(outpath, fname_format.format(N=i))
        exptime = tflat[(i - i0) % len(tflat)]
        logging.info('Taking flat {0} of {1} with exptime={2}s.'.format(i + 1, args.nflat, exptime))
        if C. take_exposure(exptime=exptime, fname=fname, shutter_open=True, latchup_action=init):
            i += 1
