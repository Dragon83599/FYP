idf_component_register(
    SRCS
        "app_main.c"
        "app_pipeline.c"
        "ble_central.c"
        "ai_stub.c"
        "display_stub.c"
    INCLUDE_DIRS
        "."
    REQUIRES
        fyp_shared   # portable LKA1/AUD1/decimator data path from ../../shared
        bt           # NimBLE host + ESP32-S3 BT controller
        nvs_flash    # bond storage (1 binding, docs/link-a-gatt-profile.md)
        esp_timer    # MTU-fallback / reconnect timers
)
