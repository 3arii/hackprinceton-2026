"""Bluesky: what people around the lake are saying, and how the people saying it are connected.

Content and connectivity are analysed together. Posts are found by search; the author
graph is built from replies, quotes, reposts, follows and near-identical text posted close
together. Communities are found with Louvain, then each is scored for signs of coordination
(copy-paste text, bursty timing, young low-follower accounts, dense ties) so organic local
reports can be told apart from campaigns. Everything is deterministic and explainable.
"""

import asyncio
import os
import re
import time
from datetime import datetime, timezone
from itertools import combinations

import httpx
import networkx as nx

from steward.config import USER_AGENT, aws

LOCAL_TERMS = re.compile(
    r"princeton|carnegie|millstone|stony brook|mercer|kingston|plainsboro|west windsor|lawrence|"
    r"rocky hill|trenton|central (new )?jersey|\bnj\b|new jersey",
    re.I,
)

_session: dict | None = None
_lock = asyncio.Lock()
_http: httpx.AsyncClient | None = None


def http() -> httpx.AsyncClient:
    global _http
    if _http is None:
        _http = httpx.AsyncClient(timeout=20, headers={"User-Agent": USER_AGENT})
    return _http


def _creds() -> tuple[str, str]:
    handle = os.environ.get("BLUESKY_HANDLE")
    password = os.environ.get("BLUESKY_APP_PASSWORD")
    if not (handle and password):
        ssm = aws("ssm")
        handle = ssm.get_parameter(Name="/buoy/bluesky-handle")["Parameter"]["Value"]
        password = ssm.get_parameter(Name="/buoy/bluesky-app-password", WithDecryption=True)["Parameter"]["Value"]
    return handle, password


async def _login(refresh: bool = False) -> dict:
    """One session per process; Bluesky rate-limits logins, so refresh instead of logging in again."""
    global _session
    async with _lock:
        if _session and not refresh:
            return _session
        if _session and refresh:
            r = await http().post(
                f"{_session['pds']}/xrpc/com.atproto.server.refreshSession",
                headers={"Authorization": f"Bearer {_session['refreshJwt']}"},
            )
            if r.status_code == 200:
                _session |= r.json()
                return _session
        handle, password = await asyncio.to_thread(_creds)
        r = await http().post(
            "https://bsky.social/xrpc/com.atproto.server.createSession",
            json={"identifier": handle, "password": password},
        )
        r.raise_for_status()
        s = r.json()
        services = (s.get("didDoc") or {}).get("service") or [{}]
        s["pds"] = services[0].get("serviceEndpoint", "https://bsky.social")
        _session = s
        return _session


async def xrpc(method: str, params: dict | list) -> dict:
    """Authenticated read through the account's PDS, which proxies to the Bluesky AppView."""
    for attempt in range(2):
        s = await _login(refresh=attempt > 0)
        r = await http().get(f"{s['pds']}/xrpc/{method}", params=params, headers={"Authorization": f"Bearer {s['accessJwt']}"})
        if r.status_code in (400, 401) and "xpired" in r.text and attempt == 0:
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("Bluesky session could not be refreshed")


def _did_of(uri: str | None) -> str | None:
    m = re.match(r"at://(did:[^/]+)/", uri or "")
    return m.group(1) if m else None


def compact(post: dict) -> dict:
    rec = post.get("record") or {}
    embed = rec.get("embed") or {}
    quoted = (embed.get("record") or {}).get("uri") or ((embed.get("record") or {}).get("record") or {}).get("uri")
    text = rec.get("text", "")
    return {
        "uri": post["uri"],
        "author": post["author"]["did"],
        "handle": post["author"]["handle"],
        "name": post["author"].get("displayName") or "",
        "time": rec.get("createdAt") or post.get("indexedAt"),
        "text": text,
        "reply_to": _did_of(((rec.get("reply") or {}).get("parent") or {}).get("uri")),
        "quotes": _did_of(quoted),
        "likes": post.get("likeCount", 0),
        "reposts": post.get("repostCount", 0),
        "replies": post.get("replyCount", 0),
        "local": bool(LOCAL_TERMS.search(text)),
    }


async def search(query: str, since: str, until: str, limit: int = 50) -> list[dict]:
    posts, cursor = [], None
    while len(posts) < limit:
        params = {"q": query, "since": since, "until": until, "sort": "latest", "limit": min(100, limit - len(posts))}
        if cursor:
            params["cursor"] = cursor
        d = await xrpc("app.bsky.feed.searchPosts", params)
        posts += [compact(p) for p in d.get("posts", [])]
        cursor = d.get("cursor")
        if not cursor or not d.get("posts"):
            break
    return posts


# --- Connectivity -----------------------------------------------------------------------


def _shingles(text: str, k: int = 3) -> set:
    words = re.findall(r"[a-z0-9']+", re.sub(r"https?://\S+", "", text.lower()))
    return {" ".join(words[i : i + k]) for i in range(max(1, len(words) - k + 1))} if words else set()


def _when(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


async def _profiles(dids: list[str]) -> dict[str, dict]:
    out = {}
    for i in range(0, len(dids), 25):
        d = await xrpc("app.bsky.actor.getProfiles", [("actors", x) for x in dids[i : i + 25]])
        for p in d.get("profiles", []):
            out[p["did"]] = p
    return out


async def _follows_among(dids: list[str]) -> list[tuple[str, str]]:
    """Who follows whom inside the author set (capped so a big conversation stays cheap)."""
    sample = dids[:25]

    async def one(actor):
        try:
            d = await xrpc("app.bsky.graph.getRelationships", [("actor", actor), *[("others", o) for o in sample if o != actor]])
        except httpx.HTTPError:
            return []
        return [(actor, r["did"]) for r in d.get("relationships", []) if r.get("following")]

    edges = await asyncio.gather(*(one(a) for a in sample))
    return [e for es in edges for e in es]


async def _reposters(uris: list[str]) -> list[tuple[str, str]]:
    async def one(uri):
        try:
            d = await xrpc("app.bsky.feed.getRepostedBy", {"uri": uri, "limit": 50})
        except httpx.HTTPError:
            return []
        return [(r["did"], _did_of(uri)) for r in d.get("repostedBy", [])]

    edges = await asyncio.gather(*(one(u) for u in uris))
    return [e for es in edges for e in es]


async def analyze(posts: list[dict], window_minutes: int = 60, dup_threshold: float = 0.55) -> dict:
    """Content + connectivity: build the author graph, find communities, score coordination."""
    started = time.time()
    if not posts:
        return {"posts": 0, "summary": "no posts to analyse"}
    authors = sorted({p["author"] for p in posts})
    by_author: dict[str, list[dict]] = {}
    for p in posts:
        by_author.setdefault(p["author"], []).append(p)

    # Content: near-identical text from different accounts within the time window
    dup_pairs = []
    sh = [(p, _shingles(p["text"])) for p in posts]
    for (a, sa), (b, sb) in combinations(sh, 2):
        if a["author"] == b["author"] or not sa or not sb:
            continue
        if abs(_when(a["time"]) - _when(b["time"])) > window_minutes * 60:
            continue
        sim = len(sa & sb) / len(sa | sb)
        if sim >= dup_threshold:
            dup_pairs.append((a, b, round(sim, 2)))

    # Connectivity: replies, quotes, reposts of the most-shared posts, follows inside the set
    top = sorted(posts, key=lambda p: p["reposts"] + p["likes"], reverse=True)[:8]
    profiles, follows, reposts = await asyncio.gather(
        _profiles(authors), _follows_among(authors), _reposters([p["uri"] for p in top if p["reposts"]])
    )

    g = nx.Graph()
    g.add_nodes_from(authors)

    def link(a, b, kind, w):
        if not a or not b or a == b:
            return
        if g.has_edge(a, b):
            g[a][b]["weight"] += w
            g[a][b]["kinds"].add(kind)
        else:
            g.add_edge(a, b, weight=w, kinds={kind})

    for p in posts:
        link(p["author"], p["reply_to"], "reply", 1)
        link(p["author"], p["quotes"], "quote", 1)
    for a, b in follows:
        link(a, b, "follow", 0.5)
    for a, b in reposts:
        link(a, b, "repost", 1)
    for a, b, sim in dup_pairs:
        link(a["author"], b["author"], "same-text", 3 * sim)

    communities = nx.community.louvain_communities(g, weight="weight", seed=7) if g.number_of_edges() else [{a} for a in authors]
    now = datetime.now(timezone.utc)

    def age_days(did):
        created = (profiles.get(did) or {}).get("createdAt")
        return (now - datetime.fromisoformat(created.replace("Z", "+00:00"))).days if created else None

    clusters = []
    for i, members in enumerate(sorted(communities, key=len, reverse=True)):
        members = set(members)
        cposts = [p for p in posts if p["author"] in members]
        if not cposts:
            continue
        times = sorted(_when(p["time"]) for p in cposts)
        dups = [d for d in dup_pairs if d[0]["author"] in members and d[1]["author"] in members]
        ages = [x for x in (age_days(m) for m in members) if x is not None]
        followers = sorted((profiles.get(m) or {}).get("followersCount", 0) for m in members)
        young = sum(1 for x in ages if x < 30) / max(1, len(ages))
        dup_share = min(1.0, 2 * len(dups) / max(1, len(cposts)))
        burst = 1.0 if len(times) > 2 and times[-1] - times[0] < window_minutes * 60 else 0.0
        density = nx.density(g.subgraph(members)) if len(members) > 1 else 0.0
        score = round(0.4 * dup_share + 0.2 * young + 0.2 * burst + 0.2 * density, 2) if len(members) >= 2 else 0.0
        if len(members) <= 2 and dup_share >= 0.5:
            assessment = "mirrored feed (automated reposting)"
        else:
            assessment = "likely coordinated" if score >= 0.5 else "mixed" if score >= 0.3 else "organic"
        clusters.append(
            {
                "id": i,
                "accounts": len(members),
                "posts": len(cposts),
                "local_posts": sum(p["local"] for p in cposts),
                "handles": sorted((profiles.get(m) or {}).get("handle", m) for m in members)[:8],
                "near_duplicate_pairs": len(dups),
                "share_of_accounts_under_30_days": round(young, 2),
                "median_followers": followers[len(followers) // 2] if followers else 0,
                "minutes_spanned": round((times[-1] - times[0]) / 60) if len(times) > 1 else 0,
                "tie_density": round(density, 2),
                "coordination_score": score,
                "assessment": assessment,
                "example": max(cposts, key=lambda p: p["likes"] + p["reposts"])["text"][:220],
            }
        )

    independent = [c for c in clusters if c["assessment"] == "organic" and c["local_posts"]]
    network = {
        "nodes": [
            {
                "id": a,
                "handle": (profiles.get(a) or {}).get("handle", by_author[a][0]["handle"]),
                "posts": len(by_author[a]),
                "followers": (profiles.get(a) or {}).get("followersCount", 0),
                "local": any(p["local"] for p in by_author[a]),
            }
            for a in authors
        ],
        "edges": [{"source": a, "target": b, "kinds": sorted(d["kinds"])} for a, b, d in g.edges(data=True) if a in by_author and b in by_author],
    }
    member_of = {m: c_i for c_i, ms in enumerate(sorted(communities, key=len, reverse=True)) for m in ms}
    for n in network["nodes"]:
        n["cluster"] = member_of.get(n["id"], -1)

    return {
        "posts": len(posts),
        "accounts": len(authors),
        "local_posts": sum(p["local"] for p in posts),
        "edges": {k: sum(1 for *_, d in g.edges(data=True) if k in d["kinds"]) for k in ("reply", "quote", "repost", "follow", "same-text")},
        "clusters": clusters[:8],
        "independent_local_voices": sum(c["accounts"] for c in independent),
        "coordinated_clusters": sum(1 for c in clusters if c["assessment"] == "likely coordinated"),
        "seconds": round(time.time() - started, 1),
        "network": network,
    }
