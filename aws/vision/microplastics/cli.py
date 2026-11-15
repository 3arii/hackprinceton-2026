import argparse
import csv
import json
from dataclasses import asdict
from pathlib import Path

import cv2

from microplastics.detect import Rig, analyze, overlay


def _rig(args):
    src = Path(args.src)
    return Rig.load(args.rig or (src / "rig.json" if src.is_dir() else None))


def _model(path):
    if not path:
        return None
    import joblib

    return joblib.load(path)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="mp", description="Microplastics in backlit intake frames")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def cmd(name, help, src=True):
        p = sub.add_parser(name, help=help)
        if src:
            p.add_argument("src", help="folder of frames, one image, or a video")
            p.add_argument("--rig", help="rig.json (default: rig.json in the frames folder)")
            p.add_argument("--blank", help="frame of clean water to use as the background")
        return p

    a = cmd("analyze", "count and size particles; prints the summary as JSON")
    a.add_argument("--model", help="classifier from `mp train`")
    a.add_argument("--overlay", metavar="DIR", help="write annotated frames here")
    a.add_argument("--csv", help="write every particle here")
    s = cmd("synth", "generate synthetic frames with ground truth", src=False)
    s.add_argument("out")
    s.add_argument("--frames", type=int, default=40)
    s.add_argument("--seed", type=int, default=0)
    e = cmd("eval", "score the detector on a folder with truth.json")
    e.add_argument("--model")
    c = cmd("crops", "cut out every particle into folders by guessed shape, for labelling")
    c.add_argument("out")
    t = cmd("train", "train a classifier from a labelled crops folder", src=False)
    t.add_argument("crops")
    f = cmd("fit", "train the clean-mesh model on photos of the clean mesh", src=False)
    f.add_argument("photos", nargs="+", help="photos, or folders of them")
    f.add_argument("--out", required=True, help="model file to write, e.g. models/<buoy-id>.npz")
    k = cmd("check", "label photos clean, microplastic or blocked; prints one JSON line each", src=False)
    k.add_argument("model")
    k.add_argument("photos", nargs="+", help="photos, or folders of them")
    k.add_argument("--overlay", metavar="DIR", help="write the photos with spots boxed here")
    args = ap.parse_args(argv)

    if args.cmd == "synth":
        from microplastics.synth import make

        print(make(args.out, args.frames, args.seed))
    elif args.cmd == "eval":
        from microplastics.evaluate import evaluate

        print(json.dumps(evaluate(args.src, _rig(args), args.blank, _model(args.model)), indent=2))
    elif args.cmd == "crops":
        from microplastics.train import crops

        print(crops(args.src, args.out, _rig(args), args.blank), "crops in", args.out)
    elif args.cmd == "train":
        from microplastics.train import train

        print(json.dumps(train(args.crops), indent=2))
    elif args.cmd == "fit":
        from microplastics import mesh

        model = mesh.fit(args.photos)
        model.save(args.out)
        print(json.dumps(model.info, indent=2))
    elif args.cmd == "check":
        from microplastics import mesh

        model, out = mesh.Model.load(args.model), Path(args.overlay) if args.overlay else None
        for f in mesh.files(args.photos):
            img = mesh.read(f)
            r = mesh.check(model, img, f.name)
            print(json.dumps(r))
            if out:
                out.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(out / f.name), mesh.overlay(img, r))
    else:
        out = Path(args.overlay) if args.overlay else None
        if out:
            out.mkdir(parents=True, exist_ok=True)

        def draw(name, frame, ps):
            if out and ps:
                cv2.imwrite(str(out / f"{Path(name).stem}.png"), overlay(frame, ps))

        particles, summary = analyze(args.src, _rig(args), args.blank, _model(args.model), draw)
        if args.csv and particles:
            with open(args.csv, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(asdict(particles[0])))
                w.writeheader()
                w.writerows(asdict(p) for p in particles)
        print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
