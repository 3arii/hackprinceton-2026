if(CONFIG_FLOTSAM_CAMERA_TEST)
    set(app_source "camera_test.c")
elseif(CONFIG_FLOTSAM_CAMERA_WIFI)
    set(app_source "camera_wifi.c")
else()
    set(app_source "main.c")
endif()

idf_component_register(SRCS "${app_source}" "camera.c"
                       INCLUDE_DIRS "."
                       REQUIRES esp32-camera mesh_lite iot_bridge esp_http_client esp_http_server
                                esp_wifi esp_event esp_timer esp_netif esp_psram nvs_flash mbedtls)
