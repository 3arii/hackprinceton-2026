"""Microplastics per litre from the mesh camera, by extrapolation.

The camera watches the intake mesh, which holds on to every particle bigger than its holes, so the
spots on it pile up as water goes through. Over a run of photos

    particles/L = (spots at the end - spots at the start) / (view_fraction x flow_lpm x minutes)

  spots          what mesh.check() found, as a running median of 3 photos, so a particle counts once
                 it shows in two of the last three and one odd photo (a bubble, a glint) changes nothing
  run            the newest stretch in which the count never drops and photos are at most MAX_GAP
                 minutes apart: a drop means the mesh was cleaned or something washed off, a gap that
                 the pump or the Pi was off. Blocked photos are skipped.
  flow_lpm       litres per minute through the mesh, from the rig config
  view_fraction  how much of the mesh's filtering area the camera sees; particles land evenly, so it
                 sees that fraction of them

It counts particles bigger than both the mesh holes and what the camera resolves, and spots that
touch count once, so a crowded mesh reads low. The 95% interval is Poisson on the particle count: a
run that caught nothing still bounds the concentration from above, more tightly the more water it
filtered.
"""

from datetime import datetime

from microplastics.detect import poisson95

MAX_GAP = 10  # minutes between usable photos before a run breaks
USABLE = {"clean", "microplastic"}


def estimate(photos, flow_lpm, view_fraction=1.0):
    """photos: (iso time, label, number of spots), in any order. None until a run spans two photos."""
    seen = sorted((datetime.fromisoformat(t), n) for t, label, n in photos if label in USABLE)
    times, count = [t for t, _ in seen], []
    for i in range(len(seen)):
        w = sorted(n for _, n in seen[max(0, i - 2) : i + 1])
        count.append(w[(len(w) - 1) // 2])

    end = start = len(seen) - 1
    while start > 0 and count[start - 1] <= count[start] and (times[start] - times[start - 1]).total_seconds() <= MAX_GAP * 60:
        start -= 1
    if end < 1 or start == end:
        return None
    minutes = (times[end] - times[start]).total_seconds() / 60
    caught = count[end] - count[start]
    liters = flow_lpm * minutes  # through the whole mesh
    sampled = view_fraction * liters  # the part whose particles the camera sees
    lo, hi = poisson95(caught)
    return {
        "per_liter": round(caught / sampled, 3),
        "ci95": [round(lo / sampled, 3), round(hi / sampled, 3)],
        "caught": caught,  # new particles in view during the run
        "on_mesh": count[end],  # particles in view now
        "liters": round(liters, 1),
        "minutes": round(minutes, 1),
        "photos": end - start + 1,
        "since": times[start].isoformat(timespec="seconds"),
        "until": times[end].isoformat(timespec="seconds"),
        "flow_lpm": flow_lpm,
        "view_fraction": view_fraction,
    }
