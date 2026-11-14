#include "camera.h"
#include "camera_pins.h"

esp_err_t flotsam_camera_init(void)
{
    const camera_config_t config = {
        .pin_pwdn = CAM_PWDN, .pin_reset = CAM_RESET,
        .pin_xclk = CAM_XCLK, .pin_sccb_sda = CAM_SDA, .pin_sccb_scl = CAM_SCL,
        .pin_d0 = CAM_D0, .pin_d1 = CAM_D1, .pin_d2 = CAM_D2, .pin_d3 = CAM_D3,
        .pin_d4 = CAM_D4, .pin_d5 = CAM_D5, .pin_d6 = CAM_D6, .pin_d7 = CAM_D7,
        .pin_vsync = CAM_VSYNC, .pin_href = CAM_HREF, .pin_pclk = CAM_PCLK,
        .xclk_freq_hz = 20000000,
        .ledc_timer = LEDC_TIMER_0, .ledc_channel = LEDC_CHANNEL_0,
        .pixel_format = PIXFORMAT_JPEG, .frame_size = FRAMESIZE_VGA,
        .jpeg_quality = 12, .fb_count = 1,
        .fb_location = CAMERA_FB_IN_PSRAM, .grab_mode = CAMERA_GRAB_WHEN_EMPTY,
    };
    return esp_camera_init(&config);
}
