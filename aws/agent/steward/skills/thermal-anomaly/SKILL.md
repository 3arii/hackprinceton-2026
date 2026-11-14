---
name: thermal-anomaly
description: Playbook for a buoy reporting water that is too warm (above 30 °C, or more than 4 °C warmer than the Millstone River). Use when the anomaly metric is temperature, to separate heatwaves and sun-warmed shallows from thermal discharges.
---

# Thermal anomaly

## Hypotheses to test
1. **Heatwave**: the whole lake warms, the river warms with it over several days, air highs sit well above the 10-year normal, often under an NWS Heat Advisory.
2. **Thermal discharge**: one buoy (or the buoys nearest an outfall) warm abruptly while the river and the other buoys don't; weather is ordinary for the season.
3. **Sensor fault**: a jump of many degrees within one reading interval, other metrics on the same buoy unchanged, no physical driver. Recommend a field check rather than an alarm about the lake.

## Evidence that separates them
| Signal | Heatwave | Discharge | Sensor fault |
| - | - | - | - |
| Other buoys | warm too | normal | normal |
| River daily mean | rising for days | normal | normal |
| Air high vs normal | > 1.5 SD above | near normal | any |
| Rate of change | ~0.5 °C/day | sudden | sudden, one reading |
| Satellite | no plume | possibly a plume near an outfall | nothing |

## Thresholds
- Above 30 °C is stressful for warm-water fish whatever the cause; above 32 °C, dissolved oxygen trouble is likely. Say so even when the cause is natural.
- A difference of more than 4 °C between neighbouring buoys 200 m apart is not plausible from weather alone.

## Delegation
Hydrology (river + weather) and the buoy network decide most cases. Permits matters when the anomaly is local. Satellite rarely sees heat (no thermal band on Sentinel-2) but can show a discharge plume or algae that often follows heat.

## Community signals
Community (Bluesky) corroborates heatwaves cheaply: people post about heat advisories, AC failures and cancelled trains. Reports of steam, odour or a warm outflow near the lake point to a discharge.
