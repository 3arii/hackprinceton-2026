#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "capture_policy.h"

int main(void)
{
    const uint8_t jpeg[] = {0xff, 0xd8, 0x00, 0xff, 0xd9};
    const uint8_t invalid[] = {0x00, 0xd8, 0xff, 0xd9};
    assert(valid_jpeg(jpeg, sizeof(jpeg)));
    assert(!valid_jpeg(NULL, 0));
    assert(!valid_jpeg(jpeg, 0) && !valid_jpeg(jpeg, 1));
    assert(!valid_jpeg(jpeg, 3));
    assert(!valid_jpeg(jpeg, sizeof(jpeg) - 1));
    assert(!valid_jpeg(invalid, sizeof(invalid)));
    assert(!valid_jpeg(jpeg, (size_t)INT_MAX + 1));

    assert(next_capture_ms(0, 0) == 30000);
    assert(next_capture_ms(0, 1) == 6000);
    assert(next_capture_ms(5999, 1) == 6000);
    assert(next_capture_ms(6000, 1) == 36000);
    assert(next_capture_ms(76000, 1) == 96000);
    for (unsigned slot = 0; slot < 5; ++slot) {
        int64_t now = INT64_C(5000000000);
        int64_t next = next_capture_ms(now, slot);
        assert(next > now && next - now <= CAPTURE_INTERVAL_MS);
        assert(next % CAPTURE_INTERVAL_MS == slot * 6000);
    }

    assert(upload_acknowledged(200) && upload_acknowledged(201) && upload_acknowledged(204));
    assert(!upload_acknowledged(202));
    assert(!upload_acknowledged(301) && !upload_acknowledged(401));
    assert(upload_retryable(true, 0));
    assert(upload_retryable(false, 408) && upload_retryable(false, 429));
    assert(upload_retryable(false, 500) && upload_retryable(false, 503));
    assert(!upload_retryable(false, 401) && !upload_retryable(false, 413));
    assert(!upload_retryable(false, 302) && !upload_retryable(false, 202));

    assert(!valid_upload_token(""));
    assert(!valid_upload_token("short"));
    assert(valid_upload_token("0123456789abcdef"));
    assert(!valid_upload_token("0123456789abcdef\r\nInjected: header"));
    assert(!valid_upload_token("0123456789abcdef with spaces"));
    char token[130];
    memset(token, 'a', sizeof(token));
    token[128] = '\0';
    assert(valid_upload_token(token));
    token[128] = 'a';
    token[129] = '\0';
    assert(!valid_upload_token(token));
    puts("capture policy checks passed");
}
