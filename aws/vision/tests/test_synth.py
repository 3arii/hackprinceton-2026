from microplastics.detect import Rig
from microplastics.evaluate import evaluate
from microplastics.synth import make


def test_detector_on_synthetic_frames(tmp_path):
    src = make(tmp_path, frames=25, seed=3)
    r = evaluate(src, Rig.load(src / "rig.json"))
    assert r["recall"] > 0.97
    assert r["shape_accuracy"] > 0.9
    assert r["bubbles_rejected"] > 0.85
    assert r["false_pos_per_frame"] < 0.2
    assert r["size_error_median"] < 0.15
    assert abs(r["measured_per_L"] / r["true_per_L"] - 1) < 0.1
