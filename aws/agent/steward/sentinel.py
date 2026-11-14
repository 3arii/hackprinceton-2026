"""Sentinel-2 L2A over Lake Carnegie, read straight from the AWS Open Data registry.

Only the lake's window of each cloud-optimized GeoTIFF is fetched (a few hundred
kilobytes per band), so one scene takes seconds. Water pixels come from the
scene classification layer and a green/NIR water index; turbidity and
chlorophyll are tracked with the standard normalized-difference indices.
"""

import asyncio
import io
from datetime import datetime, timedelta, timezone

import numpy as np
import rasterio
from PIL import Image
from rasterio.warp import transform, transform_bounds
from rasterio.windows import from_bounds

from steward.config import BUOYS, LAKE, LAKE_BBOX
from steward.lake import client

STAC = "https://earth-search.aws.element84.com/v1/search"
GDAL_ENV = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "AWS_NO_SIGN_REQUEST": "YES",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
    "GDAL_HTTP_MULTIRANGE": "YES",
    "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
}
BANDS = ("green", "red", "rededge1", "nir", "scl", "visual")
CLOUDY = {3, 8, 9, 10, 11}  # SCL: shadow, cloud medium/high, cirrus, snow


async def scenes(until: str, days_back: int = 45, max_cloud: float = 60, limit: int = 8) -> list[dict]:
    """Sentinel-2 scenes over the lake, newest first, up to `until` (ISO date)."""
    end = datetime.fromisoformat(until).replace(tzinfo=timezone.utc) + timedelta(days=1)
    start = end - timedelta(days=days_back)
    r = await client().post(
        STAC,
        json={
            "collections": ["sentinel-2-l2a"],
            "intersects": {"type": "Point", "coordinates": [LAKE["lon"], LAKE["lat"]]},
            "datetime": f"{start:%Y-%m-%dT%H:%M:%SZ}/{end:%Y-%m-%dT%H:%M:%SZ}",
            "query": {"eo:cloud_cover": {"lt": max_cloud}},
            "sortby": [{"field": "properties.datetime", "direction": "desc"}],
            "limit": limit,
        },
    )
    r.raise_for_status()
    return r.json()["features"]


def _scale(item: dict, asset: dict) -> tuple[float, float]:
    rb = (asset.get("raster:bands") or [{}])[0]
    # Earth Search already removed the +1000 BOA offset from these pixels; the metadata still lists it
    offset = 0.0 if item["properties"].get("earthsearch:boa_offset_applied") else rb.get("offset", 0.0)
    return rb.get("scale", 0.0001), offset


def _read(item: dict) -> dict:
    out = {}
    with rasterio.Env(**GDAL_ENV):
        for b in BANDS:
            asset = item["assets"][b]
            with rasterio.open(asset["href"]) as ds:
                bounds = transform_bounds("EPSG:4326", ds.crs, *LAKE_BBOX)
                win = from_bounds(*bounds, transform=ds.transform).round_offsets().round_lengths()
                arr = ds.read(window=win)
                out[b] = {"data": arr, "transform": ds.window_transform(win), "crs": ds.crs, "scale": _scale(item, asset)}
    return out


def _to_10m(a: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    up = np.kron(a, np.ones((2, 2), dtype=a.dtype))
    padded = np.zeros(shape, dtype=a.dtype)
    h, w = min(shape[0], up.shape[0]), min(shape[1], up.shape[1])
    padded[:h, :w] = up[:h, :w]
    return padded


def _analyze(item: dict, image: bool) -> dict:
    raw = _read(item)
    shape = raw["green"]["data"].shape[1:]

    def refl(b):
        scale, offset = raw[b]["scale"]
        arr = raw[b]["data"][0].astype("float32")
        if arr.shape != shape:
            arr = _to_10m(arr, shape)
        return np.where(arr > 0, arr * scale + offset, np.nan)

    g, r, re1, nir = refl("green"), refl("red"), refl("rededge1"), refl("nir")
    scl = _to_10m(raw["scl"]["data"][0], shape)
    with np.errstate(divide="ignore", invalid="ignore"):
        ndwi = (g - nir) / (g + nir)
        ndti = (r - g) / (r + g)  # turbidity: suspended sediment raises red against green
        ndci = (re1 - r) / (re1 + r)  # chlorophyll-a: algae raise red-edge against red
    clear = ~np.isin(scl, list(CLOUDY))
    # Classified water, plus narrow 10 m edges the 20 m classification misses
    water = clear & ((scl == 6) | ((ndwi > 0.2) & (nir < 0.06)))
    cloud_frac = float(1 - clear.mean())

    def stats(mask):
        if mask.sum() < 3:
            return None
        return {
            "pixels": int(mask.sum()),
            "ndti_mean": round(float(np.nanmean(ndti[mask])), 4),
            "ndci_mean": round(float(np.nanmean(ndci[mask])), 4),
            "red_reflectance": round(float(np.nanmean(r[mask])), 4),
            "green_reflectance": round(float(np.nanmean(g[mask])), 4),
        }

    # Neighbourhood around each buoy (60 m square)
    xs, ys = transform("EPSG:4326", raw["green"]["crs"], [p["lon"] for p in BUOYS.values()], [p["lat"] for p in BUOYS.values()])
    inv = ~raw["green"]["transform"]
    near = {}
    for (n, _), x, y in zip(BUOYS.items(), xs, ys):
        col, row = (int(v) for v in inv * (x, y))
        m = np.zeros(shape, bool)
        m[max(0, row - 3) : row + 4, max(0, col - 3) : col + 4] = True
        near[f"buoy_{n}"] = stats(m & water)

    result = {
        "scene": item["id"],
        "acquired": item["properties"]["datetime"],
        "scene_cloud_pct": round(item["properties"].get("eo:cloud_cover", 0), 1),
        "lake_window_cloud_pct": round(cloud_frac * 100, 1),
        "lake": stats(water),
        "near_buoys": near,
    }
    if image:
        tci = np.moveaxis(raw["visual"]["data"], 0, -1)
        # Brighten the dark water so detail shows, then outline the water mask
        img = np.clip(tci.astype("float32") * 1.6, 0, 255).astype("uint8")
        edge = water ^ np.roll(water, 1, 0) | water ^ np.roll(water, 1, 1)
        img[edge] = (90, 200, 250)
        for x, y in zip(xs, ys):
            col, row = (int(v) for v in inv * (x, y))
            img[max(0, row - 2) : row + 3, max(0, col - 2) : col + 3] = (255, 80, 60)
        buf = io.BytesIO()
        Image.fromarray(img).resize((shape[1] * 2, shape[0] * 2), Image.NEAREST).save(buf, "PNG", optimize=True)
        result["png"] = buf.getvalue()
    return result


async def analyze(item: dict, image: bool = False) -> dict:
    return await asyncio.to_thread(_analyze, item, image)
