cmake_minimum_required(VERSION 3.16)

project(fyp_software_foundation LANGUAGES C)

set(CMAKE_C_STANDARD 99)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS OFF)

add_library(fyp_audio_protocol STATIC
    shared/src/audio_protocol.c
    shared/src/link_a_protocol.c
    shared/src/host_pipeline.c
)

target_include_directories(fyp_audio_protocol
    PUBLIC
        ${CMAKE_CURRENT_SOURCE_DIR}/shared/include
)

if(MSVC)
    target_compile_options(fyp_audio_protocol PRIVATE /W4)
else()
    target_compile_options(fyp_audio_protocol PRIVATE -Wall -Wextra -Wpedantic)
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
endif()
