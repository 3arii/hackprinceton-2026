#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <limits.h>

#define CAPTURE_INTERVAL_MS INT64_C(30000)
#define UPLOAD_ATTEMPTS 3

static inline bool valid_jpeg(const uint8_t *data, size_t length)
{
    return data != NULL && length >= 4 && length <= INT_MAX &&
           data[0] == 0xff && data[1] == 0xd8 &&
           data[length - 2] == 0xff && data[length - 1] == 0xd9;
}

static inline int64_t next_capture_ms(int64_t now_ms, unsigned slot)
{
    int64_t next = now_ms / CAPTURE_INTERVAL_MS * CAPTURE_INTERVAL_MS + slot * 6000;
    return next > now_ms ? next : next + CAPTURE_INTERVAL_MS;
}

static inline bool upload_acknowledged(int status)
{
    return status == 200 || status == 201 || status == 204;
}

static inline bool upload_retryable(bool transport_failed, int status)
{
    return transport_failed || status == 408 || status == 429 ||
           (status >= 500 && status <= 599);
}

static inline bool valid_upload_token(const char *token)
{
    unsigned length = 0;
    for (; token[length]; ++length) {
        if ((unsigned char)token[length] < 33 || (unsigned char)token[length] > 126) {
            return false;
        }
    }
    return length >= 16 && length <= 128;
}
