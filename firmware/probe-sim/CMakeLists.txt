# Probe simulator: stands in for the teammate's probe until the hardware exists.
# Build with ESP-IDF 5.5:  idf.py set-target esp32s3 && idf.py build
cmake_minimum_required(VERSION 3.16)
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(probe-sim)
