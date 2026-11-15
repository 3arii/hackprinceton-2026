from datetime import datetime, timedelta

import pytest

from microplastics.concentration import estimate

T0 = datetime(2026, 11, 15, 6, 0)


def run(counts, every=1.0, label=None, start=0.0, moving=False):
    """One photo every `every` minutes with this many spots: caught particles stay put, glints move."""
    return [{
        "time": (T0 + timedelta(minutes=start + i * every)).isoformat() + "Z",
        "label": label or ("microplastic" if n else "clean"),
        "spots": [{"x": 100 + 90 * j + (137 * i % 400 if moving else 0), "y": 120 + (61 * i % 300 if moving else 0)} for j in range(n)],
        "shift": [3 * (i % 2), -2 * (i % 2)],  # the camera bobs a little
    } for i, n in enumerate(counts)]


def test_a_clean_mesh_bounds_the_concentration_from_above():
    e = estimate(run([0] * 61), flow_lpm=1.0)
    assert (e["per_liter"], e["caught"], e["liters"]) == (0, 0, 60)
    assert e["ci95"][0] == 0 and 0.05 < e["ci95"][1] < 0.07  # about 3.7 particles in 60 L


def test_particles_over_water_seen():
    # 2 particles caught in 8 minutes at 2 L/min, with the camera seeing half the mesh: 2 / 8 L
    e = estimate(run([0, 0, 0, 1, 1, 1, 2, 2, 2]), flow_lpm=2.0, view_fraction=0.5)
    assert (e["caught"], e["on_mesh"], e["liters"]) == (2, 2, 16)
    assert e["per_liter"] == pytest.approx(0.25)


def test_one_odd_photo_changes_nothing():
    assert estimate(run([0, 0, 1, 0, 0, 0]), flow_lpm=1.0)["caught"] == 0


def test_spots_that_move_between_photos_are_not_particles():
    assert estimate(run([0, 1, 1, 2, 1, 3, 2], moving=True), flow_lpm=1.0)["caught"] == 0


def test_cleaning_the_mesh_starts_a_new_run():
    e = estimate(run([3, 3, 3, 0, 0, 0, 1, 1, 1]), flow_lpm=1.0)
    assert (e["caught"], e["on_mesh"]) == (1, 1)
    assert e["since"].startswith("2026-11-15T06:04")  # where the smoothed count dropped to 0


def test_a_gap_starts_a_new_run_and_blocked_photos_are_skipped():
    photos = run([0] * 6) + run([5] * 3, label="blocked", start=6) + run([0] * 6, start=40)
    e = estimate(photos, flow_lpm=1.0)
    assert (e["minutes"], e["photos"]) == (5, 6)


def test_not_enough_photos():
    assert estimate([], flow_lpm=1.0) is None
    assert estimate(run([0]), flow_lpm=1.0) is None
    assert estimate(run([0, 0], label="blocked"), flow_lpm=1.0) is None
