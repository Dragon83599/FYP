# Compiles the repository's portable, host-tested C data path into the
# ESP-IDF firmware without touching shared/ itself.
#
# Why a wrapper component instead of EXTRA_COMPONENT_DIRS pointing at shared/:
#   - ESP-IDF only accepts a directory as a component if it contains a
#     CMakeLists.txt. shared/ has none, and the hard rule for this task is that
#     shared/ must not be modified (the root PC build owns those files).
#   - A wrapper component keeps the ESP-IDF-specific build description inside
#     firmware/ and leaves the root CMakeLists.txt untouched.
# Cost: one 12-line CMakeLists.txt and one extra component in the build graph.
# Fallback if this ever breaks: add shared/CMakeLists.txt and use
# EXTRA_COMPONENT_DIRS, which requires touching shared/ and is therefore
# currently out of scope.
#
# The sources are referenced with absolute paths so that the component works
# regardless of how deep the build directory is.
set(FYP_SHARED_DIR "${CMAKE_CURRENT_LIST_DIR}/../../../shared")

idf_component_register(
    SRCS
        "${FYP_SHARED_DIR}/src/audio_protocol.c"
        "${FYP_SHARED_DIR}/src/link_a_protocol.c"
        "${FYP_SHARED_DIR}/src/host_pipeline.c"
    INCLUDE_DIRS
        "${FYP_SHARED_DIR}/include"
)

# The shared library is integer-only and allocation-free by design (ADR-014).
# -Wall -Wextra -Wpedantic mirrors the PC build flags in the root CMakeLists.txt
# so firmware and PC builds warn about the same things. -Werror is deliberately
# not used for third-party/shared code.
target_compile_options(${COMPONENT_LIB} PRIVATE -Wall -Wextra -Wpedantic)
