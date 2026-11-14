# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Next.js (React), in `frontend/`. The monorepo's other packages are out of scope for now.

## Users

Primary audience for now: HackPrinceton 2026 judges watching a short live demo. The product itself has to read as a real, shippable product in that moment, not a prototype.

Open: the end user beyond the demo (who uses it day to day, and in what situation) has not been decided.

## Product Purpose

A HackPrinceton 2026 (Fall, Princeton University) project built for a hackathon track. Success means judges understand what it does and believe it within the demo.

Open: the track or sponsor prize, the core idea, and what the product does are not decided yet. Do not invent them.

## Positioning

Open: the differentiating mechanism has not been stated.

## Operating Context

- Built during the hackathon, which started 2026-11-13 at 8:30 PM ET. All code must be written after that time; the GitHub repo (`3arii/hackprinceton-2026`) was created at 8:38 PM ET.
- Shown in a short live demo to judges, most likely on a laptop screen or a projector.

## Capabilities and Constraints

- First confirmed capability: a scrollable, real-world map, dark, with 3D buildings.
- Map stack: MapLibre GL JS with OpenFreeMap vector tiles (OpenStreetMap data, no API key). Upgrade path if a richer look is needed later: Mapbox GL with the Standard style, which needs an access token.
- Open: the backend, data sources, sponsor APIs, and the rest of the feature set.

## Brand Commitments

The user's binding visual direction, in their words: "dark mode, simple, apple, unicorn startup."

Open: product name, logo, and voice.

## Evidence on Hand

None yet. Don't fabricate users, metrics, testimonials, partners, or sponsor endorsements.

## Product Principles

1. Demo-first: every screen must make sense to a judge seeing it for the first time, within seconds.
2. Real over mocked: show real places and real data wherever possible, and label anything synthetic as synthetic.
3. Restraint reads as confidence: simple, few controls, nothing decorative that doesn't do a job.
