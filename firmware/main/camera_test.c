#include <stdio.h>
#include <unistd.h>
#include "mbedtls/base64.h"
#include "sdkconfig.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_psram.h"
#include "camera.h"
#include "capture_policy.h"

static const char *TAG = "camera_test";

static void export_image(void)
{
    camera_fb_t *frame = esp_camera_fb_get();
    if (frame == NULL) {
        ESP_LOGE(TAG, "USB capture failed");
        return;
    }
    esp_camera_fb_return(frame);
    frame = esp_camera_fb_get();
    if (frame == NULL) {
        ESP_LOGE(TAG, "Fresh USB capture failed");
        return;
    }
    if (frame->format != PIXFORMAT_JPEG || !valid_jpeg(frame->buf, frame->len)) {
        ESP_LOGE(TAG, "USB capture is not a complete JPEG");
        esp_camera_fb_return(frame);
        return;
    }
    printf("FLOTSAM_JPEG_BEGIN %u\n", (unsigned)frame->len);
    for (size_t offset = 0; offset < frame->len; offset += 192) {
        unsigned char encoded[257];
        size_t encoded_length = 0;
        size_t length = frame->len - offset;
        if (length > 192) length = 192;
        int err = mbedtls_base64_encode(encoded, sizeof(encoded), &encoded_length,
                                       frame->buf + offset, length);
        if (err != 0) {
            ESP_LOGE(TAG, "USB encoding failed: %d", err);
            esp_camera_fb_return(frame);
            return;
        }
        encoded[encoded_length] = '\0';
        printf("FLOTSAM_JPEG_DATA %s\n", encoded);
    }
    puts("FLOTSAM_JPEG_END");
    fflush(stdout);
    esp_camera_fb_return(frame);
}

void app_main(void)
{
#if !CONFIG_FLOTSAM_CAMERA_PINMAP_CONFIRMED
    ESP_LOGE(TAG, "FAIL: verify camera_pins.h and confirm the pin map in menuconfig");
    return;
#endif
    if (!esp_psram_is_initialized()) {
        ESP_LOGE(TAG, "FAIL: PSRAM unavailable; enable Octal PSRAM in menuconfig");
        return;
    }
    ESP_LOGI(TAG, "PSRAM: %u bytes", (unsigned)esp_psram_get_size());
    esp_err_t err = flotsam_camera_init();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "FAIL: camera initialization: %s; check ribbon and pin map", esp_err_to_name(err));
        return;
    }
    sensor_t *sensor = esp_camera_sensor_get();
    if (sensor == NULL) {
        ESP_LOGE(TAG, "FAIL: no sensor detected");
        esp_camera_deinit();
        return;
    }
    ESP_LOGI(TAG, "Sensor PID: 0x%04x (OV3660 is 0x3660)", sensor->id.PID);

    unsigned passed = 0;
    for (unsigned i = 0; i < 3; ++i) {
        camera_fb_t *frame = esp_camera_fb_get();
        if (frame == NULL) {
            ESP_LOGE(TAG, "FAIL: capture %u timed out", i + 1);
            break;
        }
        bool valid = frame->format == PIXFORMAT_JPEG &&
                     frame->width == 640 && frame->height == 480 &&
                     valid_jpeg(frame->buf, frame->len);
        ESP_LOGI(TAG, "Frame %u: %ux%u, %u bytes, %s", i + 1,
                 (unsigned)frame->width, (unsigned)frame->height,
                 (unsigned)frame->len, valid ? "JPEG OK" : "INVALID JPEG");
        esp_camera_fb_return(frame);
        if (!valid) break;
        ++passed;
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
    if (passed != 3) {
        err = esp_camera_deinit();
        ESP_LOGE(TAG, "FAIL: %u/3 frames passed, camera shutdown: %s", passed, esp_err_to_name(err));
        return;
    }
    ESP_LOGI(TAG, "PASS: 3/3 VGA JPEG frames captured; send p over USB to export a fresh image");
    for (;;) {
        char command;
        if (read(STDIN_FILENO, &command, 1) == 1 && command == 'p') {
            export_image();
        }
        vTaskDelay(pdMS_TO_TICKS(20));
    }
}
