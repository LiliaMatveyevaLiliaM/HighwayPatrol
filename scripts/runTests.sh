#!/bin/bash

# Common packages
python -m unittest tests/stacks/common/src/python/orangeUtils/testLoggerSetup.py

# Collector packages
python -m unittest tests/stacks/collector/src/python/testStillsGrabber.py
python -m unittest tests/stacks/collector/src/python/testVideosGrabber.py
python -m unittest tests/stacks/collector/src/python/testYoutubeInterface.py

# Drover packages
python -m unittest tests/stacks/drover/src/python/testMain.py
