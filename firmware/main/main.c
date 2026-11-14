#include <assert.h>
#include <inttypes.h>
#include <stdio.h>
#include <string.h>

#include "sdkconfig.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"
#include "esp_bridge.h"
#include "esp_camera.h"
#include "esp_crt_bundle.h"
#include "esp_event.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_mesh_lite.h"
#include "esp_netif.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "nvs_flash.h"

#include "camera.h"
#include "capture_policy.h"

static const char *TAG = "flotsam";
static EventGroupHandle_t network_events;
#define CONNECTED_BIT BIT0

static bool configuration_valid(void)
{
#if !CONFIG_FLOTSAM_CAMERA_PINMAP_CONFIRMED
    ESP_LOGE(TAG, "Verify camera_pins.h, then confirm the pin map in menuconfig");
    return false;
#endif
    size_t ssid_len = strlen(CONFIG_FLOTSAM_PI_SSID);
    size_t pi_password_len = strlen(CONFIG_FLOTSAM_PI_PASSWORD);
    size_t mesh_password_len = strlen(CONFIG_BRIDGE_SOFTAP_PASSWORD);
    if (ssid_len == 0 || ssid_len > 32 || pi_password_len < 8 || pi_password_len > 63 ||
        mesh_password_len < 8 || mesh_password_len > 63) {
        ESP_LOGE(TAG, "Set Pi SSID/password and Bridge SoftAP password in menuconfig");
        return false;
    }
    if (!valid_upload_token(CONFIG_FLOTSAM_UPLOAD_TOKEN)) {
        ESP_LOGE(TAG, "Set a 16–128 character upload token without spaces/control characters");
        return false;
    }
    if (strncmp(CONFIG_FLOTSAM_RECEIVER_URL, "http://", 7) != 0 &&
        strncmp(CONFIG_FLOTSAM_RECEIVER_URL, "https://", 8) != 0) {
        ESP_LOGE(TAG, "Receiver URL must use HTTP or HTTPS");
        return false;
    }
    return true;
}

static void network_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        const ip_event_got_ip_t *event = data;
        xEventGroupSetBits(network_events, CONNECTED_BIT);
        ESP_LOGI(TAG, "IP " IPSTR ", mesh level %u", IP2STR(&event->ip_info.ip),
                 (unsigned)esp_mesh_lite_get_level());
    } else {
        xEventGroupClearBits(network_events, CONNECTED_BIT);
        ESP_LOGW(TAG, "Uplink lost; mesh handles reconnection");
    }
}

static void start_mesh(void)
{
    // IoT-Bridge 1.0.1 logs Wi-Fi passwords at INFO level.
    esp_log_level_set("bridge_wifi", ESP_LOG_WARN);
    ESP_ERROR_CHECK(nvs_flash_init());
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    network_events = xEventGroupCreate();
    assert(network_events != NULL);
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, network_event, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_LOST_IP, network_event, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, WIFI_EVENT_STA_DISCONNECTED, network_event, NULL));
    esp_bridge_create_all_netif();
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));

    wifi_config_t station = {0};
    memcpy(station.sta.ssid, CONFIG_FLOTSAM_PI_SSID, strlen(CONFIG_FLOTSAM_PI_SSID));
    memcpy(station.sta.password, CONFIG_FLOTSAM_PI_PASSWORD, strlen(CONFIG_FLOTSAM_PI_PASSWORD));
    station.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;
    ESP_ERROR_CHECK(esp_bridge_wifi_set_config(WIFI_IF_STA, &station));

    wifi_config_t access_point = {.ap = {
        .ssid = CONFIG_BRIDGE_SOFTAP_SSID,
        .password = CONFIG_BRIDGE_SOFTAP_PASSWORD,
        .authmode = WIFI_AUTH_WPA2_PSK,
        .max_connection = CONFIG_BRIDGE_SOFTAP_MAX_CONNECT_NUMBER,
    }};
    ESP_ERROR_CHECK(esp_bridge_wifi_set_config(WIFI_IF_AP, &access_point));

    esp_mesh_lite_config_t mesh = ESP_MESH_LITE_DEFAULT_INIT();
    mesh.join_mesh_ignore_router_status = true;
    esp_mesh_lite_init(&mesh);

    uint8_t mac[6];
    ESP_ERROR_CHECK(esp_wifi_get_mac(WIFI_IF_AP, mac));
    char ssid[33];
    snprintf(ssid, sizeof(ssid), "Flotsam-%02x%02x%02x", mac[3], mac[4], mac[5]);
    ESP_ERROR_CHECK(esp_mesh_lite_set_softap_info(ssid, CONFIG_BRIDGE_SOFTAP_PASSWORD));
#if CONFIG_FLOTSAM_MESH_ROOT
    ESP_ERROR_CHECK(esp_mesh_lite_set_allowed_level(1));
#else
    ESP_ERROR_CHECK(esp_mesh_lite_set_disallowed_level(1));
#endif
    esp_mesh_lite_start();
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
}

static bool upload_image(const camera_fb_t *frame, const char *node_id,
                         const char *image_id, int64_t captured_ms)
{
    char authorization[sizeof(CONFIG_FLOTSAM_UPLOAD_TOKEN) + 8];
    snprintf(authorization, sizeof(authorization), "Bearer %s", CONFIG_FLOTSAM_UPLOAD_TOKEN);
    char uptime[24], level[8];
    snprintf(uptime, sizeof(uptime), "%" PRId64, captured_ms);
    snprintf(level, sizeof(level), "%u", (unsigned)esp_mesh_lite_get_level());

    for (unsigned attempt = 0; attempt < UPLOAD_ATTEMPTS; ++attempt) {
        if (!(xEventGroupGetBits(network_events) & CONNECTED_BIT)) {
            return false;
        }
        const esp_http_client_config_t config = {
            .url = CONFIG_FLOTSAM_RECEIVER_URL, .method = HTTP_METHOD_POST,
            .timeout_ms = 20000, .disable_auto_redirect = true,
            .crt_bundle_attach = esp_crt_bundle_attach,
        };
        esp_http_client_handle_t client = esp_http_client_init(&config);
        if (client == NULL) {
            ESP_LOGE(TAG, "Cannot allocate HTTP client");
            return false;
        }
        esp_err_t err = esp_http_client_set_header(client, "Content-Type", "image/jpeg");
        if (err == ESP_OK) err = esp_http_client_set_header(client, "Authorization", authorization);
        if (err == ESP_OK) err = esp_http_client_set_header(client, "X-Node-ID", node_id);
        if (err == ESP_OK) err = esp_http_client_set_header(client, "X-Image-ID", image_id);
        if (err == ESP_OK) err = esp_http_client_set_header(client, "X-Capture-Uptime-Ms", uptime);
        if (err == ESP_OK) err = esp_http_client_set_header(client, "X-Mesh-Level", level);
        if (err == ESP_OK) err = esp_http_client_set_post_field(client, (const char *)frame->buf, frame->len);
        if (err == ESP_OK) err = esp_http_client_perform(client);
        int status = esp_http_client_get_status_code(client);
        esp_http_client_cleanup(client);

        if (err == ESP_OK && upload_acknowledged(status)) {
            ESP_LOGI(TAG, "Delivered %s (%u bytes)", image_id, (unsigned)frame->len);
            return true;
        }
        ESP_LOGW(TAG, "Upload attempt %u: %s, HTTP %d", attempt + 1, esp_err_to_name(err), status);
        if (!upload_retryable(err != ESP_OK, status) || attempt + 1 == UPLOAD_ATTEMPTS) {
            return false;
        }
        vTaskDelay(pdMS_TO_TICKS(500U << attempt));
    }
    return false;
}

void app_main(void)
{
    if (!configuration_valid()) return;
    start_mesh();
    esp_err_t err = flotsam_camera_init();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Camera init failed: %s; mesh relay stays active", esp_err_to_name(err));
        return;
    }

    uint8_t mac[6];
    ESP_ERROR_CHECK(esp_wifi_get_mac(WIFI_IF_STA, mac));
    char node_id[13], boot_id[17];
    snprintf(node_id, sizeof(node_id), "%02x%02x%02x%02x%02x%02x", MAC2STR(mac));
    snprintf(boot_id, sizeof(boot_id), "%08" PRIx32 "%08" PRIx32, esp_random(), esp_random());
    uint64_t sequence = 0;
    ESP_LOGI(TAG, "Camera %s, slot %d, 640x480 JPEG every 30s; external illumination required",
             node_id, CONFIG_FLOTSAM_NODE_SLOT);

    for (;;) {
        // shortcut: slots are boot-relative; use shared time if precise synchronization is needed.
        int64_t now = esp_timer_get_time() / 1000;
        int64_t due = next_capture_ms(now, CONFIG_FLOTSAM_NODE_SLOT);
        vTaskDelay(pdMS_TO_TICKS(due - now));
        if (!(xEventGroupGetBits(network_events) & CONNECTED_BIT)) {
            ESP_LOGW(TAG, "Skipping capture: no uplink");
            continue;
        }

        // Discard the buffered frame so a 30-second-old image is never uploaded as new.
        camera_fb_t *frame = esp_camera_fb_get();
        if (frame == NULL) {
            ESP_LOGW(TAG, "Camera did not return a frame");
            continue;
        }
        esp_camera_fb_return(frame);
        frame = esp_camera_fb_get();
        if (frame == NULL) {
            ESP_LOGW(TAG, "Fresh capture failed");
            continue;
        }
        if (frame->format != PIXFORMAT_JPEG || !valid_jpeg(frame->buf, frame->len)) {
            ESP_LOGW(TAG, "Discarding invalid JPEG frame");
            esp_camera_fb_return(frame);
            continue;
        }
        char image_id[64];
        snprintf(image_id, sizeof(image_id), "%s-%s-%" PRIu64, node_id, boot_id, ++sequence);
        int64_t captured_ms = (int64_t)frame->timestamp.tv_sec * 1000 + frame->timestamp.tv_usec / 1000;
        if (!upload_image(frame, node_id, image_id, captured_ms)) {
            // shortcut: undelivered images are dropped; add SD storage if lossless collection is required.
            ESP_LOGW(TAG, "Dropped %s after upload failure", image_id);
        }
        esp_camera_fb_return(frame);
    }
}
