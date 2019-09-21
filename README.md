# Ethernet Driver for SBIG STXL Cameras

This is a python package that serves as a driver for STXL cameras connected via ethernet.  All communication
with the camera is via HTTP requests to the camera's built-in web server.

## Installation

To install, use:
```
pip install stxldriver
```
This package requires python >= 3.5 as well as the numpy and requests packages.

This package has only been tested on STXL-6303 cameras, but should work for other STX/STXL cameras with
the same built-in web server.

## Usage

All communication with a camera requires that you first create a driver object,  e.g.
```
from stxldriver.camera import Camera
C = Camera(URL='http://10.0.0.3')
```
Most of the low-level functionality available through the web server can be access using `Camera` methods,
but you can normally just use this higher-level API:
```
C.initialize(reboot=True, binning=2, temperature_setpoint=15)
C.take_exposure(exptime=0, fname='zero.fits', shutter_open=False)
C.take_exposure(exptime=10, fname='dark.fits', shutter_open=False)
C.take_exposure(exptime=100, fname='science.fits', shutter_open=True)
```
If you have a filter wheel installed, you can also control through the same object, e.g.
```
C.set_filter(filter_number=3)
```
All output is sent to the standard python [logging](https://docs.python.org/3/library/logging.html) facility, so you can easily [control how much you want to see and where it goes](https://docs.python.org/3/howto/logging.html).

See the comments in `stxldriver/camera.py` for more detailed documentation. The scripts described below also provide useful
examples of using this driver.

## Scripts

This package includes two command-line scripts: `stxlcalib` and `stxlstress`.  The first is to automatically collect a sequence of calibration zeros, darks and flats.  The second starts a long-running "stress test" of your camera and this driver, performing a repeated cycle of open-shutter exposures.  Use this to checkout a camera or observe the temperature latchup recovery described below.

## Known Issues

### Temperature Latchup

All STXL-6303 cameras I have used periodically experience "temperature latchup", where the temperature is significantly higher than the setpoint and the cooling power is at 100%.  The `take_exposure` method demonstrated above can automatically detect and recover from this condition, without requiring a power cycle.  The recommended pattern for this is:
```
C = Camera(...)
init = lambda: C.initialize(...)
init()
success = C.take_exposure(..., latchup_action=init)
```
This approach will automatically detect a latchup, and reboot and reconfigure the camera when one occurs, then return `False`.

### Fan Setpoint

Writing a new value of the fan setpoint is not reliable, and will sometimes appear to succeed but actually not change the previous value.  For this reason, the `initialize` method will not complain when this happens, but you will see an `INFO` logging  message like:
```
09/20/2019 17:13:49 INFO wrote FanSetpoint=100.0 but read 63.0.
09/20/2019 17:13:49 INFO wrote Fan=2 but read 1.
```
Note that this is only an issue if you are trying to set a manual fan speed, and setting the more critical temperature setpoint apopears to be reliable.

To handle this in your own code, if you are working at a lower level than `initialize` and `take_exposure`, you will need to catch the `RuntimeError` that is raised whenever any configuration data is not correctly updated, e.g.
```
try:
    self.write_setup(Fan=2, FanSetpoint=100.0)
catch RuntimeError:
    pass
```
