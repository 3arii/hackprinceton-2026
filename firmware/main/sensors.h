#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

// One sensor sample. Fields the hardware could not provide are marked invalid, never faked.
typedef struct {
    bool temp_ok;
    float temp_c;              // DS18B20 on GPIO 0 (adapter board has the 4.7k pull-up)
    bool light_ok;
    int light_mv;              // TEMT6000 on GPIO 1 with the sample line ON (turbidity LED lit)
    int ambient_mv;            // TEMT6000 with the sample line OFF (daylight leak)
    bool cond_ok;
    int cond_mv;               // conductivity divider on GPIO 14 (ADC2: unavailable while Wi-Fi transmits)
} flotsam_reading_t;

esp_err_t flotsam_sensors_init(void);

// Turns the sample line (pump + turbidity LED + conductivity excitation) on for sample_ms,
// converts temperature meanwhile, reads light and conductivity, then turns it off.
void flotsam_sensors_read(flotsam_reading_t *out, unsigned sample_ms);

// Writes the reading as one line of JSON (no newline). Returns the length, or 0 if it did not fit.
size_t flotsam_reading_json(const flotsam_reading_t *r, const char *node_id, uint32_t seq,
                            int64_t uptime_ms, int rssi, unsigned level, char *buf, size_t len);
