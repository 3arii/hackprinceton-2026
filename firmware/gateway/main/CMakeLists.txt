idf_component_register(SRCS "gateway.c" INCLUDE_DIRS "." "../../main"
    REQUIRES esp_wifi esp_event esp_netif esp_http_server esp_timer nvs_flash
             esp_driver_usb_serial_jtag mbedtls)
