# HTTP Driver for SBIG STXL Cameras

This is a python package that serves as a driver for STXL cameras connected via ethernet.  All communication
with the camera is via HTTP requests to the camera's built-in web server.

To install, use:
```
pip install stxldriver
```
This package requires python >= 3.5 as well as the numpy and requests packages.

This package has only been tested on STXL-6303 cameras, but should work for other STX/STXL cameras with
the same built-in web server.
