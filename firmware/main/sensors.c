// Buoy sensors on the ESP32S3 CAM: DS18B20 temperature (GPIO 0), TEMT6000 light (GPIO 1),
// conductivity divider (GPIO 14), and the sample line (GPIO 47) that runs pump + turbidity LED +
// conductivity excitation together. Pins match leo.txt / HARDWARE-EVERYTHING.md.

#include "sensors.h"

#include <inttypes.h>
#include <math.h>
#include <stdio.h>

#include "sdkconfig.h"
#include "driver/gpio.h"
#include "esp_adc/adc_cali.h"
#include "esp_adc/adc_cali_scheme.h"
#include "esp_adc/adc_oneshot.h"
#include "esp_log.h"
#include "esp_rom_sys.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define PIN_TEMP   CONFIG_FLOTSAM_TEMP_GPIO
#define PIN_LIGHT  CONFIG_FLOTSAM_LIGHT_GPIO
#define PIN_COND   CONFIG_FLOTSAM_COND_GPIO
#define PIN_SAMPLE CONFIG_FLOTSAM_SAMPLE_GPIO
#define ADC_SAMPLES 32

static const char *TAG = "sensors";
static portMUX_TYPE onewire_lock = portMUX_INITIALIZER_UNLOCKED;

typedef struct {
    adc_oneshot_unit_handle_t unit;
    adc_channel_t channel;
    adc_cali_handle_t cali;
    bool ok;
} analog_t;
static analog_t light, cond;

// ---------------------------------------------------------------- 1-Wire (bit-banged, open drain)
static inline void ow_low(void) { gpio_set_level(PIN_TEMP, 0); }
static inline void ow_release(void) { gpio_set_level(PIN_TEMP, 1); }
static inline int ow_sample(void) { return gpio_get_level(PIN_TEMP); }

static bool ow_reset(void)
{
    portENTER_CRITICAL(&onewire_lock);
    ow_low();
    esp_rom_delay_us(480);
    ow_release();
    esp_rom_delay_us(70);
    int presence = !ow_sample();
    portEXIT_CRITICAL(&onewire_lock);
    esp_rom_delay_us(410);
    return presence;
}

static void ow_write_bit(int bit)
{
    portENTER_CRITICAL(&onewire_lock);
    ow_low();
    esp_rom_delay_us(bit ? 6 : 60);
    ow_release();
    portEXIT_CRITICAL(&onewire_lock);
    esp_rom_delay_us(bit ? 64 : 10);
}

static int ow_read_bit(void)
{
    portENTER_CRITICAL(&onewire_lock);
    ow_low();
    esp_rom_delay_us(6);
    ow_release();
    esp_rom_delay_us(9);
    int bit = ow_sample();
    portEXIT_CRITICAL(&onewire_lock);
    esp_rom_delay_us(55);
    return bit;
}

static void ow_write(uint8_t byte)
{
    for (int i = 0; i < 8; ++i) ow_write_bit((byte >> i) & 1);
}

static uint8_t ow_read(void)
{
    uint8_t byte = 0;
    for (int i = 0; i < 8; ++i) byte |= ow_read_bit() << i;
    return byte;
}

static uint8_t crc8(const uint8_t *data, size_t len)
{
    uint8_t crc = 0;
    while (len--) {
        uint8_t in = *data++;
        for (int i = 0; i < 8; ++i) {
            uint8_t mix = (crc ^ in) & 1;
            crc >>= 1;
            if (mix) crc ^= 0x8C;
            in >>= 1;
        }
    }
    return crc;
}

static bool ds18b20_start(void)
{
    if (!ow_reset()) return false;
    ow_write(0xCC);   // skip ROM: one probe on the bus
    ow_write(0x44);   // convert T (12-bit, up to 750 ms)
    return true;
}

static bool ds18b20_finish(float *celsius)
{
    if (!ow_reset()) return false;
    ow_write(0xCC);
    ow_write(0xBE);   // read scratchpad
    uint8_t s[9];
    for (int i = 0; i < 9; ++i) s[i] = ow_read();
    bool all_ones = true;
    for (int i = 0; i < 9; ++i) all_ones &= s[i] == 0xFF;
    if (all_ones || crc8(s, 8) != s[8]) return false;
    int16_t raw = (int16_t)((s[1] << 8) | s[0]);
    if (raw == 0x0550) return false;   // 85.0 C power-on value: conversion did not run
    *celsius = raw / 16.0f;
    return true;
}

// ---------------------------------------------------------------- ADC
static void analog_init(analog_t *a, int gpio)
{
    adc_unit_t unit;
    if (adc_oneshot_io_to_channel(gpio, &unit, &a->channel) != ESP_OK) {
        ESP_LOGE(TAG, "GPIO %d is not an ADC pin", gpio);
        return;
    }
    adc_oneshot_unit_init_cfg_t unit_cfg = {.unit_id = unit, .ulp_mode = ADC_ULP_MODE_DISABLE};
    if (adc_oneshot_new_unit(&unit_cfg, &a->unit) != ESP_OK) return;
    adc_oneshot_chan_cfg_t chan = {.atten = ADC_ATTEN_DB_12, .bitwidth = ADC_BITWIDTH_DEFAULT};
    if (adc_oneshot_config_channel(a->unit, a->channel, &chan) != ESP_OK) return;
    adc_cali_curve_fitting_config_t cali = {.unit_id = unit, .chan = a->channel,
                                            .atten = ADC_ATTEN_DB_12, .bitwidth = ADC_BITWIDTH_DEFAULT};
    if (adc_cali_create_scheme_curve_fitting(&cali, &a->cali) != ESP_OK) a->cali = NULL;
    a->ok = true;
}

// Averages ADC_SAMPLES reads; returns false if the ADC refused (ADC2 while Wi-Fi is busy).
static bool analog_mv(analog_t *a, int *mv)
{
    if (!a->ok) return false;
    int sum = 0, good = 0;
    for (int i = 0; i < ADC_SAMPLES; ++i) {
        int raw;
        if (adc_oneshot_read(a->unit, a->channel, &raw) != ESP_OK) continue;
        int v = raw;
        if (a->cali && adc_cali_raw_to_voltage(a->cali, raw, &v) != ESP_OK) continue;
        if (!a->cali) v = raw * 3100 / 4095;
        sum += v;
        ++good;
    }
    if (good < ADC_SAMPLES / 2) return false;
    *mv = sum / good;
    return true;
}

// ---------------------------------------------------------------- public
esp_err_t flotsam_sensors_init(void)
{
    gpio_config_t sample = {.pin_bit_mask = 1ULL << PIN_SAMPLE, .mode = GPIO_MODE_OUTPUT};
    ESP_ERROR_CHECK(gpio_config(&sample));
    gpio_set_level(PIN_SAMPLE, 0);

    gpio_config_t onewire = {.pin_bit_mask = 1ULL << PIN_TEMP, .mode = GPIO_MODE_INPUT_OUTPUT_OD,
                             .pull_up_en = GPIO_PULLUP_ENABLE};
    ESP_ERROR_CHECK(gpio_config(&onewire));
    ow_release();

    analog_init(&light, PIN_LIGHT);
    analog_init(&cond, PIN_COND);
    ESP_LOGI(TAG, "temp GPIO %d, light GPIO %d, conductivity GPIO %d, sample line GPIO %d",
             PIN_TEMP, PIN_LIGHT, PIN_COND, PIN_SAMPLE);
    return ESP_OK;
}

void flotsam_sensors_read(flotsam_reading_t *out, unsigned sample_ms)
{
    *out = (flotsam_reading_t){0};
    out->light_ok = analog_mv(&light, &out->ambient_mv);   // LED off first: daylight baseline

    gpio_set_level(PIN_SAMPLE, 1);                          // pump + LED + excitation on together
    bool converting = ds18b20_start();
    TickType_t wait = pdMS_TO_TICKS(sample_ms < 800 ? 800 : sample_ms);
    vTaskDelay(wait);                                        // water moving, LED settled, conversion done
    out->light_ok = analog_mv(&light, &out->light_mv) && out->light_ok;
    out->cond_ok = analog_mv(&cond, &out->cond_mv);
    gpio_set_level(PIN_SAMPLE, 0);

    out->temp_ok = converting && ds18b20_finish(&out->temp_c);
}

size_t flotsam_reading_json(const flotsam_reading_t *r, const char *node_id, uint32_t seq,
                            int64_t uptime_ms, int rssi, unsigned level, char *buf, size_t len)
{
    char temp[16] = "null", lit[12] = "null", amb[12] = "null", cnd[12] = "null";
    if (r->temp_ok) snprintf(temp, sizeof(temp), "%.2f", r->temp_c);
    if (r->light_ok) {
        snprintf(lit, sizeof(lit), "%d", r->light_mv);
        snprintf(amb, sizeof(amb), "%d", r->ambient_mv);
    }
    if (r->cond_ok) snprintf(cnd, sizeof(cnd), "%d", r->cond_mv);
    int n = snprintf(buf, len,
                     "{\"node\":\"%s\",\"seq\":%" PRIu32 ",\"uptime_ms\":%" PRId64 ",\"temp_c\":%s,"
                     "\"light_mv\":%s,\"ambient_mv\":%s,\"cond_mv\":%s,\"rssi\":%d,\"level\":%u}",
                     node_id, seq, uptime_ms, temp, lit, amb, cnd, rssi, level);
    return n > 0 && (size_t)n < len ? (size_t)n : 0;
}
