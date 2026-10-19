idf_component_register(
    SRCS
        "app_main.c"
        "app_pipeline.c"
        "ble_central.c"
        "ai_stub.c"
        "display_stub.c"
        "display_st7789.c"   # hardware panel path (ESP-IDF built-in ST7789 driver)
    INCLUDE_DIRS
        "."
    REQUIRES
        fyp_shared   # portable LKA1/AUD1/decimator data path from ../../shared
        bt           # NimBLE host + ESP32-S3 BT controller
        nvs_flash    # bond storage (1 binding, docs/link-a-gatt-profile.md)
        esp_timer    # MTU-fallback / reconnect timers
    PRIV_REQUIRES
        esp_lcd      # esp_lcd_new_panel_st7789() + SPI panel IO (ADR-019)
)
