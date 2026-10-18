cmake_minimum_required(VERSION 3.16)

project(fyp_software_foundation LANGUAGES C)

set(CMAKE_C_STANDARD 99)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS OFF)

add_library(fyp_audio_protocol STATIC
    shared/src/audio_protocol.c
    shared/src/link_a_protocol.c
    shared/src/host_pipeline.c
    shared/src/hr_detector.c
)

target_include_directories(fyp_audio_protocol
    PUBLIC
        ${CMAKE_CURRENT_SOURCE_DIR}/shared/include
)

if(MSVC)
    target_compile_options(fyp_audio_protocol PRIVATE /W4)
else()
    target_compile_options(fyp_audio_protocol PRIVATE -Wall -Wextra -Wpedantic)
    # hr_detector.c needs sqrtf()/isfinite(); host_pipeline.c stays integer-only.
    target_link_libraries(fyp_audio_protocol PUBLIC m)
endif()

include(CTest)

if(BUILD_TESTING)
    add_executable(test_audio_protocol
        tests/test_audio_protocol.c
    )
    target_link_libraries(test_audio_protocol PRIVATE fyp_audio_protocol)

    add_executable(test_link_a_protocol
        tests/test_link_a_protocol.c
    )
    target_link_libraries(test_link_a_protocol PRIVATE fyp_audio_protocol)

    add_executable(test_host_pipeline
        tests/test_host_pipeline.c
    )
    target_link_libraries(test_host_pipeline PRIVATE fyp_audio_protocol)
    if(MSVC)
        target_compile_options(test_host_pipeline PRIVATE /W4)
    else()
        target_compile_options(test_host_pipeline PRIVATE -Wall -Wextra -Wpedantic)
        target_link_libraries(test_host_pipeline PRIVATE m)
    endif()

    add_test(NAME audio_protocol COMMAND test_audio_protocol)
    add_test(NAME link_a_protocol COMMAND test_link_a_protocol)
    add_test(NAME host_pipeline COMMAND test_host_pipeline)

    add_executable(test_hr_detector
        tests/test_hr_detector.c
    )
    target_link_libraries(test_hr_detector PRIVATE fyp_audio_protocol)
    if(MSVC)
        target_compile_options(test_hr_detector PRIVATE /W4)
    else()
        target_compile_options(test_hr_detector PRIVATE -Wall -Wextra -Wpedantic)
        target_link_libraries(test_hr_detector PRIVATE m)
    endif()
    # The ctest entry is the synthetic/reference unit suite. The CirCor
    # agreement run is data-dependent and is invoked explicitly:
    #   build/test_hr_detector --fixture data/reports/hr-fixture-circor.csv
    add_test(NAME hr_detector COMMAND test_hr_detector)

    # End-to-end composition test: 8 kHz PCM -> AUD1 -> LKA1 -> host_pipeline
    # -> hr_detector. The synthetic cases need no data; the CirCor comparison
    # is skipped with a message when data/processed/circor/recordings.csv is
    # absent (as in CI).
    add_executable(test_end_to_end_chain
        tests/test_end_to_end_chain.c
    )
    target_link_libraries(test_end_to_end_chain PRIVATE fyp_audio_protocol)
    if(MSVC)
        target_compile_options(test_end_to_end_chain PRIVATE /W4)
    else()
        target_compile_options(test_end_to_end_chain PRIVATE -Wall -Wextra -Wpedantic)
        target_link_libraries(test_end_to_end_chain PRIVATE m)
    endif()
    target_compile_definitions(test_end_to_end_chain PRIVATE
        TEST_E2E_SOURCE_DIR="${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME end_to_end_chain COMMAND test_end_to_end_chain)
endif()
