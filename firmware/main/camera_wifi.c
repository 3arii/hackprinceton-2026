#include <stdio.h>
#include <string.h>
#include "sdkconfig.h"
#include "esp_event.h"
#include "esp_http_server.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "nvs_flash.h"
#include "camera.h"
#include "capture_policy.h"

static const char *TAG = "camera_wifi";

static esp_err_t show_page(httpd_req_t *request)
{
    static const char page[] =
        "<!doctype html><html lang=en><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        "<meta http-equiv=refresh content=30><title>Flotsam camera</title>"
        "<body><h1>Flotsam camera</h1><p>Leave this page open for a fresh picture every 30 seconds.</p>"
        "<img src='/capture' width=640 height=480 style='max-width:100%;height:auto' "
        "alt='Latest picture from the buoy camera'>"
        "<p><a href='/capture' download='flotsam.jpg'>Save a fresh JPEG</a></p></body></html>";
    esp_err_t err = httpd_resp_set_type(request, "text/html; charset=utf-8");
    if (err == ESP_OK) err = httpd_resp_set_hdr(request, "Cache-Control", "no-store");
    if (err == ESP_OK) err = httpd_resp_send(request, page, HTTPD_RESP_USE_STRLEN);
    return err;
}

static esp_err_t send_image(httpd_req_t *request)
{
    camera_fb_t *frame = esp_camera_fb_get();
    if (frame == NULL) {
        return httpd_resp_send_err(request, HTTPD_500_INTERNAL_SERVER_ERROR, "Camera capture failed");
    }
    // Discard the queued frame so each browser request receives a fresh image.
    esp_camera_fb_return(frame);
    frame = esp_camera_fb_get();
    if (frame == NULL) {
        return httpd_resp_send_err(request, HTTPD_500_INTERNAL_SERVER_ERROR, "Fresh capture failed");
    }
    if (frame->format != PIXFORMAT_JPEG || !valid_jpeg(frame->buf, frame->len)) {
        esp_camera_fb_return(frame);
        return httpd_resp_send_err(request, HTTPD_500_INTERNAL_SERVER_ERROR, "Incomplete JPEG");
    }
    esp_err_t err = httpd_resp_set_type(request, "image/jpeg");
    if (err == ESP_OK) err = httpd_resp_set_hdr(request, "Cache-Control", "no-store");
    if (err == ESP_OK) err = httpd_resp_send(request, (const char *)frame->buf, frame->len);
    ESP_LOGI(TAG, "JPEG: %u bytes, delivery: %s", (unsigned)frame->len, esp_err_to_name(err));
    esp_camera_fb_return(frame);
    return err;
}

void app_main(void)
{
#if !CONFIG_FLOTSAM_CAMERA_PINMAP_CONFIRMED
    ESP_LOGE(TAG, "Verify camera_pins.h and confirm the pin map in menuconfig");
    return;
#endif
    size_t password_length = strlen(CONFIG_FLOTSAM_AP_PASSWORD);
    if (password_length < 8 || password_length > 63) {
        ESP_LOGE(TAG, "Set a WPA2 hotspot password of 8-63 characters");
        return;
    }
    ESP_ERROR_CHECK(nvs_flash_init());
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_t *ap = esp_netif_create_default_wifi_ap();
    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&init));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));

    uint8_t mac[6];
    ESP_ERROR_CHECK(esp_read_mac(mac, ESP_MAC_WIFI_SOFTAP));
    wifi_config_t wifi = {.ap = {
        .channel = 1, .max_connection = 1, .authmode = WIFI_AUTH_WPA2_PSK,
    }};
    snprintf((char *)wifi.ap.ssid, sizeof(wifi.ap.ssid), "Flotsam-Camera-%02X%02X%02X",
             mac[3], mac[4], mac[5]);
    wifi.ap.ssid_len = strlen((char *)wifi.ap.ssid);
    memcpy(wifi.ap.password, CONFIG_FLOTSAM_AP_PASSWORD, password_length);
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_AP));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_AP, &wifi));
    ESP_ERROR_CHECK(esp_wifi_start());

    esp_err_t err = flotsam_camera_init();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Camera initialization failed: %s", esp_err_to_name(err));
        esp_wifi_stop();
        return;
    }
    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    config.stack_size = 8192;
    config.max_uri_handlers = 2;
    config.lru_purge_enable = true;
    httpd_handle_t server = NULL;
    ESP_ERROR_CHECK(httpd_start(&server, &config));
    const httpd_uri_t page = {.uri = "/", .method = HTTP_GET, .handler = show_page};
    const httpd_uri_t capture = {.uri = "/capture", .method = HTTP_GET, .handler = send_image};
    ESP_ERROR_CHECK(httpd_register_uri_handler(server, &page));
    ESP_ERROR_CHECK(httpd_register_uri_handler(server, &capture));
    esp_netif_ip_info_t ip;
    ESP_ERROR_CHECK(esp_netif_get_ip_info(ap, &ip));
    ESP_LOGI(TAG, "Join %s (2.4 GHz WPA2); open http://" IPSTR,
             wifi.ap.ssid, IP2STR(&ip.ip));
}
