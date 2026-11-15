#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "sdkconfig.h"
#include "driver/usb_serial_jtag.h"
#include "esp_event.h"
#include "esp_http_server.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "mbedtls/base64.h"
#include "mbedtls/sha256.h"
#include "nvs_flash.h"
#include "capture_policy.h"

#define MAX_JPEG (128 * 1024)

static bool identifier(const char *value, size_t min, size_t max)
{
    size_t n = strlen(value);
    if (n < min || n > max) return false;
    for (size_t i = 0; i < n; ++i) {
        char c = value[i];
        if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || c == '-')) return false;
    }
    return true;
}

static bool usb_send(const char *line, int64_t deadline)
{
    size_t left = strlen(line);
    while (left && esp_timer_get_time() < deadline) {
        int sent = usb_serial_jtag_write_bytes(line, left, pdMS_TO_TICKS(100));
        if (sent > 0) { line += sent; left -= sent; }
    }
    return left == 0;
}

static bool host_saved(const char *node, const char *image)
{
    char expected[128], line[128];
    snprintf(expected, sizeof(expected), "FLOTSAM_ACK %s %s", node, image);
    size_t used = 0;
    int64_t deadline = esp_timer_get_time() + 5000000;
    while (esp_timer_get_time() < deadline) {
        char c;
        if (usb_serial_jtag_read_bytes(&c, 1, pdMS_TO_TICKS(50)) != 1) continue;
        if (c == '\n') {
            line[used] = 0;
            if (strcmp(line, expected) == 0) return true;
            used = 0;
        } else if (c != '\r') {
            if (used + 1 >= sizeof(line)) return false;
            line[used++] = c;
        }
    }
    return false;
}

static esp_err_t receive_image(httpd_req_t *req)
{
    char authorization[160], type[32], node[13], image[80];
    char expected[] = "Bearer " CONFIG_FLOTSAM_GATEWAY_TOKEN;
    if (httpd_req_get_hdr_value_str(req, "Authorization", authorization, sizeof(authorization)) != ESP_OK ||
        strcmp(authorization, expected) != 0) {
        httpd_resp_send_err(req, HTTPD_401_UNAUTHORIZED, "Invalid upload token");
        return ESP_FAIL;
    }
    if (httpd_req_get_hdr_value_str(req, "Content-Type", type, sizeof(type)) != ESP_OK ||
        strcmp(type, "image/jpeg") != 0 ||
        httpd_req_get_hdr_value_str(req, "X-Node-ID", node, sizeof(node)) != ESP_OK ||
        httpd_req_get_hdr_value_str(req, "X-Image-ID", image, sizeof(image)) != ESP_OK ||
        !identifier(node, 12, 12) || !identifier(image, 1, 79) ||
        req->content_len < 4 || req->content_len > MAX_JPEG) {
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Invalid JPEG metadata or size");
        return ESP_FAIL;
    }
    uint8_t *jpeg = malloc(req->content_len);
    if (!jpeg) {
        httpd_resp_set_status(req, "503 Service Unavailable");
        httpd_resp_sendstr(req, "Image buffer unavailable");
        return ESP_FAIL;
    }
    size_t received = 0;
    int64_t deadline = esp_timer_get_time() + 10000000;
    while (received < req->content_len && esp_timer_get_time() < deadline) {
        int n = httpd_req_recv(req, (char *)jpeg + received, req->content_len - received);
        if (n <= 0) break;
        received += n;
    }
    if (received != req->content_len || !valid_jpeg(jpeg, received)) {
        free(jpeg);
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Incomplete JPEG");
        return ESP_FAIL;
    }
    uint8_t digest[32];
    char sha[65], line[320];
    if (mbedtls_sha256(jpeg, received, digest, 0) != 0) {
        free(jpeg);
        return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Hash failed");
    }
    for (unsigned i = 0; i < sizeof(digest); ++i) snprintf(sha + i * 2, 3, "%02x", digest[i]);
    snprintf(line, sizeof(line), "\nFLOTSAM_BEGIN %s %s %u %s\n", node, image, (unsigned)received, sha);
    deadline = esp_timer_get_time() + 10000000;
    bool sent = usb_send(line, deadline);
    for (size_t offset = 0; sent && offset < received; offset += 192) {
        unsigned char encoded[257];
        size_t encoded_length, n = received - offset;
        if (n > 192) n = 192;
        sent = mbedtls_base64_encode(encoded, sizeof(encoded), &encoded_length, jpeg + offset, n) == 0;
        if (sent) {
            encoded[encoded_length] = 0;
            snprintf(line, sizeof(line), "FLOTSAM_DATA %s\n", encoded);
            sent = usb_send(line, deadline);
        }
    }
    free(jpeg);
    if (!sent || !usb_send("FLOTSAM_END\n", deadline) || !host_saved(node, image)) {
        httpd_resp_set_status(req, "503 Service Unavailable");
        return httpd_resp_sendstr(req, "Laptop has not saved image");
    }
    httpd_resp_set_status(req, "201 Created");
    return httpd_resp_sendstr(req, "Saved on USB host");
}

// Sensor readings: one small JSON object per POST, forwarded as a single USB line
//   FLOTSAM_READING <node-id> <json>
// Readings are a live stream, so the gateway answers 204 once the line is written to USB.
static esp_err_t receive_reading(httpd_req_t *req)
{
    char authorization[160], type[40], node[13], body[384];
    char expected[] = "Bearer " CONFIG_FLOTSAM_GATEWAY_TOKEN;
    if (httpd_req_get_hdr_value_str(req, "Authorization", authorization, sizeof(authorization)) != ESP_OK ||
        strcmp(authorization, expected) != 0) {
        httpd_resp_send_err(req, HTTPD_401_UNAUTHORIZED, "Invalid upload token");
        return ESP_FAIL;
    }
    if (httpd_req_get_hdr_value_str(req, "Content-Type", type, sizeof(type)) != ESP_OK ||
        strncmp(type, "application/json", 16) != 0 ||
        httpd_req_get_hdr_value_str(req, "X-Node-ID", node, sizeof(node)) != ESP_OK ||
        !identifier(node, 12, 12) || req->content_len < 2 || req->content_len >= sizeof(body)) {
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Invalid reading metadata or size");
        return ESP_FAIL;
    }
    size_t received = 0;
    while (received < req->content_len) {
        int n = httpd_req_recv(req, body + received, req->content_len - received);
        if (n <= 0) break;
        received += n;
    }
    body[received] = 0;
    bool printable = received == req->content_len && body[0] == '{' && body[received - 1] == '}';
    for (size_t i = 0; printable && i < received; ++i) printable = body[i] >= 32 && body[i] <= 126;
    if (!printable) {
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Reading must be one line of JSON");
        return ESP_FAIL;
    }
    char line[440];
    snprintf(line, sizeof(line), "\nFLOTSAM_READING %s %s\n", node, body);
    if (!usb_send(line, esp_timer_get_time() + 2000000)) {
        httpd_resp_set_status(req, "503 Service Unavailable");
        return httpd_resp_sendstr(req, "USB host not reading");
    }
    httpd_resp_set_status(req, "204 No Content");
    return httpd_resp_send(req, NULL, 0);
}

void app_main(void)
{
    size_t password_len = strlen(CONFIG_FLOTSAM_GATEWAY_PASSWORD);
    size_t ssid_len = strlen(CONFIG_FLOTSAM_GATEWAY_SSID);
    if (password_len < 8 || password_len > 63 || ssid_len < 1 || ssid_len > 32 ||
        !valid_upload_token(CONFIG_FLOTSAM_GATEWAY_TOKEN)) {
        ESP_LOGE("gateway", "Configure gateway SSID, WPA2 password, and upload token");
        return;
    }
    ESP_ERROR_CHECK(nvs_flash_init());
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_ap();
    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&init));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    wifi_config_t wifi = {.ap = {.channel = 1, .max_connection = 5, .authmode = WIFI_AUTH_WPA2_PSK}};
    memcpy(wifi.ap.ssid, CONFIG_FLOTSAM_GATEWAY_SSID, ssid_len);
    wifi.ap.ssid_len = ssid_len;
    memcpy(wifi.ap.password, CONFIG_FLOTSAM_GATEWAY_PASSWORD, password_len);
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_AP));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_AP, &wifi));
    ESP_ERROR_CHECK(esp_wifi_start());
    usb_serial_jtag_driver_config_t usb = {.tx_buffer_size = 4096, .rx_buffer_size = 512};
    ESP_ERROR_CHECK(usb_serial_jtag_driver_install(&usb));
    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    config.stack_size = 8192;
    config.recv_wait_timeout = 3;
    config.lru_purge_enable = true;
    httpd_handle_t server;
    ESP_ERROR_CHECK(httpd_start(&server, &config));
    httpd_uri_t upload = {.uri = "/images", .method = HTTP_POST, .handler = receive_image};
    ESP_ERROR_CHECK(httpd_register_uri_handler(server, &upload));
    httpd_uri_t readings = {.uri = "/readings", .method = HTTP_POST, .handler = receive_reading};
    ESP_ERROR_CHECK(httpd_register_uri_handler(server, &readings));
    ESP_LOGI("gateway", "Ready: %s; JPEGs to /images, sensor readings to /readings -> USB host", CONFIG_FLOTSAM_GATEWAY_SSID);
}
