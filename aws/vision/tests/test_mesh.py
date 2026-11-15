import cv2
import numpy as np
import pytest

from microplastics import mesh


def _scene(rng, h=480, w=640):
    """A defocused backlit mesh: bright holes on a vignetted blue glow, as the buoy camera sees it."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    glow = np.clip(1 - 0.7 * np.hypot((xx - w / 2) / w, (yy - h / 2) / h) ** 2, 0, 1)
    holes = np.zeros((h, w), np.float32)
    for cy in range(140, 400, 110):
        for cx in range(180, 480, 75):
            cv2.ellipse(holes, (cx + int(rng.integers(-6, 7)), cy), (22, 34), 0, 0, 360, 1.0, -1)
    return glow, holes


def _photo(glow, holes, rng, shift=(0.0, 0.0), dark=None):
    m = np.float32([[1, 0, shift[0]], [0, 1, shift[1]]])
    lit = cv2.warpAffine(holes, m, holes.shape[::-1], borderMode=cv2.BORDER_REFLECT)
    if dark is not None:
        lit = lit * (1 - dark)
    img = np.stack([0.85 * glow + 0.3 * lit, 0.25 * glow + 0.6 * lit, 0.4 * glow + 0.6 * lit], 2) * 255
    if dark is not None:
        img *= (1 - dark)[..., None]
    img = cv2.GaussianBlur(img, (0, 0), 6) * rng.uniform(0.97, 1.03) + rng.normal(0, 1.5, img.shape)
    _, buf = cv2.imencode(".jpg", np.clip(img, 0, 255).astype(np.uint8), [cv2.IMWRITE_JPEG_QUALITY, 60])
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


@pytest.fixture(scope="module")
def setup(tmp_path_factory):
    rng = np.random.default_rng(1)
    glow, holes = _scene(rng)
    d = tmp_path_factory.mktemp("clean")
    for i in range(10):
        cv2.imwrite(str(d / f"clean_{i}.jpg"), _photo(glow, holes, rng, rng.uniform(-3, 3, 2)))
    return mesh.fit([d]), glow, holes, rng


def test_new_clean_photos_are_clean(setup):
    model, glow, holes, rng = setup
    assert all(model.info["leave_one_out"].values())
    for _ in range(5):
        r = mesh.check(model, _photo(glow, holes, rng, rng.uniform(-3, 3, 2)))
        assert r["label"] == "clean", r


def test_a_particle_on_the_mesh_is_found(setup):
    model, glow, holes, rng = setup
    for x, y, size in ((255, 140, 14), (330, 360, 20), (420, 250, 30)):
        dark = np.zeros(holes.shape, np.float32)
        cv2.circle(dark, (x, y), size // 2, 0.9, -1)
        r = mesh.check(model, _photo(glow, holes, rng, rng.uniform(-3, 3, 2), dark))
        assert r["label"] == "microplastic", r
        assert any(abs(s["x"] - x) < 30 and abs(s["y"] - y) < 30 and s["darker"] for s in r["spots"]), r


def test_a_moved_or_covered_camera_is_blocked(setup):
    model, glow, holes, rng = setup
    assert mesh.check(model, _photo(glow, holes, rng, (60, 40)))["label"] == "blocked"
    assert mesh.check(model, np.full((480, 640, 3), 8, np.uint8))["label"] == "blocked"


def test_save_and_load(setup, tmp_path):
    model, glow, holes, rng = setup
    model.save(tmp_path / "m.npz")
    again = mesh.Model.load(tmp_path / "m.npz")
    photo = _photo(glow, holes, rng)
    assert mesh.check(again, photo) == mesh.check(model, photo)
