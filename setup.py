from setuptools import setup

setup(
    name='stxldriver',
    version='0.1.dev0',
    description='HTTP Driver for SBIG STXL Cameras',
    url='http://github.com/dkirkby/STXLDriver',
    author='David Kirkby',
    author_email='dkirkby@uci.edu',
    license='MIT',
    packages=['stxldriver'],
    install_requires=['numpy', 'requests'],
    entry_points = {
        'console_scripts': [
            'stxlcalib=stxldriver.scripts.calibrate:main',
        ],
    }
)