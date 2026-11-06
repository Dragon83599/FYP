idf_component_register(
    SRCS "probe_sim.c" "lka1_encode.c"
    INCLUDE_DIRS "."
    EMBED_FILES "sample.wav"
    REQUIRES bt nvs_flash esp_timer)
