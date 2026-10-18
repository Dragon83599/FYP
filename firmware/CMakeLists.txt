# ESP-IDF project for the PCG stethoscope host (ESP32-S3).
#
# This is a standalone ESP-IDF project. It intentionally does not include the
# repository root CMakeLists.txt (that one is the host/PC build for the shared
# portable library and its ctest suite) and it does not modify shared/, tests/,
# tools/ or docs/. The portable data path is pulled in through
# components/fyp_shared, which compiles the unmodified shared/src/*.c files.
#
# Build (after activating the ESP-IDF environment, see README.md):
#   idf.py set-target esp32s3
#   idf.py build
cmake_minimum_required(VERSION 3.16)

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(fyp_stethoscope_host)
