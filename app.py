import asyncio
import base64
import json
import logging
import os
import re
import time
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import quote, unquote

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

APP_NAME = "Chinese & HK Catalog v1.6.1"
APP_VERSION = "1.6.1"
# Plain link only: no widget script, so the pages stay fast and nothing is tracked.
SUPPORT_URL = "https://ko-fi.com/yongtwinchin"

# httpx logs full request URLs at INFO, which would include a v3 `api_key` parameter.
logging.getLogger("httpx").setLevel(logging.WARNING)

TMDB_API_TOKEN = os.getenv("TMDB_API_TOKEN", "").strip()
TMDB_API_KEY = os.getenv("TMDB_API_KEY", "").strip()

TMDB_BASE = "https://api.themoviedb.org/3"
TMDB_IMAGE = "https://image.tmdb.org/t/p"
CINEMETA_BASE = "https://v3-cinemeta.strem.io"

SORT_OPTIONS = ["Popular", "Latest", "Top Rated"]

# TMDB genre IDs are stable; labels are what Stremio shows in the dropdown.
# Picking a genre lists that genre by popularity (the dropdown holds one value).
TV_GENRES = {
    "剧情": "18",
    "喜剧": "35",
    "犯罪": "80",
    "悬疑": "9648",
    "动作冒险": "10759",
    "科幻奇幻": "10765",
    "动画": "16",
    "家庭": "10751",
    "战争政治": "10768",
    "纪录": "99",
    "真人秀": "10764",
    "儿童": "10762",
}
MOVIE_GENRES = {
    "剧情": "18",
    "喜剧": "35",
    "动作": "28",
    "爱情": "10749",
    "犯罪": "80",
    "惊悚": "53",
    "悬疑": "9648",
    "恐怖": "27",
    "奇幻": "14",
    "科幻": "878",
    "动画": "16",
    "历史": "36",
    "战争": "10752",
    "家庭": "10751",
}


def genre_map(tmdb_type: str) -> dict[str, str]:
    return MOVIE_GENRES if tmdb_type == "movie" else TV_GENRES


ALL_GENRE_LABELS = set(TV_GENRES) | set(MOVIE_GENRES)


CATALOGS = {
    ("movie", "cn_movies"): {
        "name": "🇨🇳 中国电影",
        "country": "CN",
        "language": "zh-CN",
        "tmdb_type": "movie",
    },
    ("movie", "hk_movies"): {
        "name": "🇭🇰 香港电影",
        "country": "HK",
        "language": "zh-HK",
        "tmdb_type": "movie",
    },
    ("movie", "tw_movies"): {
        "name": "🇹🇼 台湾电影",
        "country": "TW",
        "language": "zh-TW",
        "tmdb_type": "movie",
    },
    ("series", "cn_series"): {
        "name": "🇨🇳 中国电视剧",
        "country": "CN",
        "language": "zh-CN",
        "tmdb_type": "tv",
    },
    ("series", "hk_series"): {
        "name": "🇭🇰 香港电视剧",
        "country": "HK",
        "language": "zh-HK",
        "tmdb_type": "tv",
    },
    ("series", "tw_series"): {
        "name": "🇹🇼 台湾电视剧",
        "country": "TW",
        "language": "zh-TW",
        "tmdb_type": "tv",
    },
    # Genre rows: subsets of cn_series, so they offer no search (it would repeat
    # the 中国电视剧 results) and no genre dropdown, only sorting.
    ("series", "cn_donghua"): {
        "name": "🇨🇳 国产动画",
        "country": "CN",
        "language": "zh-CN",
        "tmdb_type": "tv",
        "with_genres": "16",
    },
    ("series", "cn_variety"): {
        "name": "🇨🇳 综艺节目",
        "country": "CN",
        "language": "zh-CN",
        "tmdb_type": "tv",
        "with_genres": "10764|10767",
    },
}

CATALOG_IDS = [catalog_id for _, catalog_id in CATALOGS]
SCRIPTS = {
    "auto": "按地区 (CN 简体 · HK/TW 繁體)",
    "hans": "简体中文优先",
    "hant": "繁體中文優先",
}


@dataclass(frozen=True)
class AddonConfig:
    """Per-install settings, carried in the install URL path (see /configure)."""

    script: str = "auto"
    catalogs: tuple[str, ...] = tuple(CATALOG_IDS)
    tmdb_key: str = ""

    def encode(self) -> str:
        data: dict[str, Any] = {"s": self.script, "c": list(self.catalogs)}
        if self.tmdb_key:
            data["k"] = self.tmdb_key
        raw = json.dumps(data, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    def __repr__(self) -> str:  # keep the key out of any log line or traceback
        return f"AddonConfig(script={self.script!r}, catalogs={self.catalogs!r})"


DEFAULT_CONFIG = AddonConfig()
current_config: ContextVar[AddonConfig] = ContextVar("current_config", default=DEFAULT_CONFIG)

# A TMDB v3 API key is 32 hex characters; a v4 read access token is a JWT.
TMDB_V3_KEY = re.compile(r"[0-9a-f]{32}")
TMDB_V4_TOKEN = re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")


def valid_tmdb_key(key: Any) -> bool:
    return isinstance(key, str) and bool(
        TMDB_V3_KEY.fullmatch(key) or TMDB_V4_TOKEN.fullmatch(key)
    )


def decode_config(segment: str) -> AddonConfig | None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{4,1024}", segment or ""):
        return None
    try:
        padded = segment + "=" * (-len(segment) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None

    script = data.get("s", "auto")
    catalogs = data.get("c", CATALOG_IDS)
    if script not in SCRIPTS or not isinstance(catalogs, list):
        return None

    tmdb_key = data.get("k", "")
    if tmdb_key != "" and not valid_tmdb_key(tmdb_key):
        return None

    selected = tuple(cid for cid in CATALOG_IDS if cid in set(catalogs))
    if not selected:
        return None
    return AddonConfig(script=script, catalogs=selected, tmdb_key=tmdb_key)


def manifest_catalog(media_type: str, catalog_id: str, cfg: dict[str, Any]) -> dict[str, Any]:
    options = list(SORT_OPTIONS)
    if not cfg.get("with_genres"):
        options += list(genre_map(cfg["tmdb_type"]))

    extra: list[dict[str, Any]] = [
        {"name": "genre", "isRequired": False, "options": options, "optionsLimit": 1},
        {"name": "skip", "isRequired": False},
    ]
    if not cfg.get("with_genres"):
        # Lets Stremio's search bar find titles by their Chinese names.
        extra.append({"name": "search", "isRequired": False})

    return {"type": media_type, "id": catalog_id, "name": cfg["name"], "extra": extra}


def build_manifest(config: AddonConfig = DEFAULT_CONFIG) -> dict[str, Any]:
    return {
        # Must stay equal to the deployed ID so existing installs update in place.
        "id": "com.twin.chinese-hk-catalog.v141",
        "version": APP_VERSION,
        "name": APP_NAME,
        "description": (
            "China/Hong Kong/Taiwan movie and TV catalogs with Chinese metadata, "
            "search, localized credits, logos, trailers and episode titles. "
            "Other IMDb titles remain in Cinemeta English. "
            "Support: ko-fi.com/yongtwinchin"
        ),
        "resources": [
            "catalog",
            {"name": "meta", "types": ["movie", "series"], "idPrefixes": ["tch:"]},
        ],
        "types": ["movie", "series"],
        "catalogs": [
            manifest_catalog(media_type, catalog_id, cfg)
            for (media_type, catalog_id), cfg in CATALOGS.items()
            if catalog_id in config.catalogs
        ],
        "idPrefixes": ["tch:"],
        "behaviorHints": {"adult": False, "p2p": False, "configurable": True},
    }


MANIFEST = build_manifest()


class SimpleTTLCache:
    def __init__(self):
        self._data: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        row = self._data.get(key)
        if not row:
            return None
        expires_at, value = row
        if expires_at <= time.time():
            self._data.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        self._data[key] = (time.time() + ttl_seconds, value)


cache = SimpleTTLCache()
http = httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=8.0))
tmdb_sem = asyncio.Semaphore(10)

# Optional shared cache. Vercel's in-memory cache is lost whenever a new instance
# starts, so a cold detail page refetches everything from TMDB. Connecting an
# Upstash Redis (Vercel Storage / Marketplace) sets these variables; without them
# the addon works exactly as before, memory-only.
REDIS_URL = (
    os.getenv("KV_REST_API_URL") or os.getenv("UPSTASH_REDIS_REST_URL") or ""
).strip().rstrip("/")
REDIS_TOKEN = (
    os.getenv("KV_REST_API_TOKEN") or os.getenv("UPSTASH_REDIS_REST_TOKEN") or ""
).strip()
REDIS_PREFIX = "tch:v1:"
REDIS_MAX_BYTES = 900_000  # Upstash's request limit is 1 MB


async def redis_command(*args: Any) -> Any:
    if not REDIS_URL or not REDIS_TOKEN:
        return None
    try:
        response = await http.post(
            REDIS_URL,
            json=[str(arg) for arg in args],
            headers={"Authorization": f"Bearer {REDIS_TOKEN}"},
            timeout=2.0,
        )
        response.raise_for_status()
        return response.json().get("result")
    except Exception:
        # The shared cache is an optimisation; never fail a request because of it.
        return None


async def cache_get(key: str) -> Any | None:
    value = cache.get(key)
    if value is not None:
        return value

    raw = await redis_command("GET", REDIS_PREFIX + key)
    if not isinstance(raw, str):
        return None
    try:
        stored = json.loads(raw)
        expires_at = float(stored["e"])
        value = stored["v"]
    except Exception:
        return None

    remaining = int(expires_at - time.time())
    if remaining <= 0 or value is None:
        return None
    cache.set(key, value, remaining)
    return value


async def cache_set(key: str, value: Any, ttl_seconds: int) -> None:
    cache.set(key, value, ttl_seconds)
    if not REDIS_URL:
        return
    try:
        raw = json.dumps(
            {"e": time.time() + ttl_seconds, "v": value},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError):
        return
    if len(raw.encode()) <= REDIS_MAX_BYTES:
        await redis_command("SET", REDIS_PREFIX + key, raw, "EX", ttl_seconds)


app = FastAPI(title=APP_NAME, version=APP_VERSION)

# Starlette's CORSMiddleware only adds Access-Control-Allow-Origin when the request
# carries an Origin header. Our responses are `Cache-Control: public`, so Vercel's CDN
# would cache whichever copy came first; a request without Origin (curl, a native app,
# a server-side proxy) then poisoned the cached copy for every browser, and Stremio Web
# showed "No metadata was found!" with a CORS error. Always send the CORS headers.
CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "*",
}


@app.middleware("http")
async def always_cors(request: Request, call_next):
    if request.method == "OPTIONS":
        return Response(status_code=204, headers=CORS_HEADERS)
    response = await call_next(request)
    response.headers.update(CORS_HEADERS)
    return response


def require_tmdb_credentials(user_key: str = "") -> None:
    if not user_key and not TMDB_API_TOKEN and not TMDB_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="TMDB is not configured. Set TMDB_API_TOKEN (recommended) or TMDB_API_KEY in Vercel.",
        )


async def tmdb_get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    # An install made on /configure carries its own TMDB key; installs without one
    # (the root manifest) keep using the server's key.
    user_key = current_config.get().tmdb_key
    require_tmdb_credentials(user_key)
    params = dict(params or {})
    headers = {"accept": "application/json"}

    if user_key:
        if user_key.startswith("eyJ"):
            headers["Authorization"] = f"Bearer {user_key}"
        else:
            params["api_key"] = user_key
    elif TMDB_API_TOKEN:
        headers["Authorization"] = f"Bearer {TMDB_API_TOKEN}"
    else:
        params["api_key"] = TMDB_API_KEY

    async with tmdb_sem:
        response = await http.get(f"{TMDB_BASE}{path}", params=params, headers=headers)

    if response.status_code == 404:
        return {}

    if response.status_code == 401 and user_key:
        # Never fall back to the server's key: that would hide a broken install.
        raise HTTPException(
            status_code=502,
            detail=(
                "TMDB rejected the API key saved in this install. "
                "Open the addon's Configure page and enter a valid key."
            ),
        )

    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        # httpx's message contains the full URL, which holds a v3 `api_key`.
        raise httpx.HTTPStatusError(
            f"TMDB returned {response.status_code} for {path}",
            request=exc.request,
            response=exc.response,
        ) from None
    return response.json()


async def cinemeta_meta(media_type: str, imdb_id: str) -> dict[str, Any] | None:
    cache_key = f"cinemeta:{media_type}:{imdb_id}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        response = await http.get(
            f"{CINEMETA_BASE}/meta/{media_type}/{imdb_id}.json",
            headers={"accept": "application/json"},
        )
        response.raise_for_status()
        meta = response.json().get("meta")
    except Exception:
        meta = None

    cache.set(cache_key, meta, 24 * 60 * 60)
    return meta


def image_url(path: str | None, size: str = "w500") -> str | None:
    return f"{TMDB_IMAGE}/{size}{path}" if path else None


def contains_cjk(text: str | None) -> bool:
    if not isinstance(text, str) or not text:
        return False
    return bool(re.search(r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]", text))


def parse_extra_args(extra_args: str | None) -> dict[str, str]:
    """Parse Stremio's `key=value&key=value.json` extra path segment."""
    values: dict[str, str] = {}
    if not extra_args:
        return values

    for part in extra_args.removesuffix(".json").replace("&", "/").split("/"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        values[unquote(key)] = unquote(value)

    return values


def parse_extra(extra_args: str | None) -> tuple[int, str]:
    values = parse_extra_args(extra_args)

    try:
        skip = max(0, int(values.get("skip", 0)))
    except ValueError:
        skip = 0

    # The single dropdown holds either a sort order or a genre label.
    selection = values.get("genre", "Popular")
    if selection not in SORT_OPTIONS and selection not in ALL_GENRE_LABELS:
        selection = "Popular"

    return skip, selection


def parse_search(extra_args: str | None) -> str:
    return " ".join(parse_extra_args(extra_args).get("search", "").split())[:100]


def discover_sort_params(tmdb_type: str, sort_mode: str) -> dict[str, str]:
    if sort_mode == "Latest":
        if tmdb_type == "movie":
            return {
                "sort_by": "primary_release_date.desc",
                "primary_release_date.lte": date.today().isoformat(),
            }

        return {
            "sort_by": "first_air_date.desc",
            "first_air_date.lte": date.today().isoformat(),
        }

    if sort_mode == "Top Rated":
        return {
            "sort_by": "vote_average.desc",
            "vote_count.gte": "50",
        }

    return {"sort_by": "popularity.desc"}


async def imdb_id_for_tmdb(tmdb_type: str, tmdb_id: int) -> str | None:
    cache_key = f"external:{tmdb_type}:{tmdb_id}"
    cached = await cache_get(cache_key)
    if cached is not None:
        return cached or None

    data = await tmdb_get(f"/{tmdb_type}/{tmdb_id}/external_ids")
    imdb_id = data.get("imdb_id")

    if not isinstance(imdb_id, str) or not re.fullmatch(r"tt\d+", imdb_id):
        imdb_id = None

    await cache_set(cache_key, imdb_id or "", 7 * 24 * 60 * 60)
    return imdb_id


def make_custom_id(media_type: str, tmdb_id: int, imdb_id: str | None) -> str:
    """
    Use an addon-specific meta ID so Stremio does not let Cinemeta win the detail-page
    metadata selection for the same IMDb `tt...` ID.

    The canonical IMDb ID is still embedded and later used as the stream/video ID.
    Titles TMDB cannot link to IMDb (common for mainland dramas and donghua) use
    `tch:<type>:<tmdb-id>` and stream as `tmdb:<id>` instead.
    """
    if imdb_id:
        return f"tch:{media_type}:{tmdb_id}:{imdb_id}"
    return f"tch:{media_type}:{tmdb_id}"


def parse_custom_id(media_type: str, item_id: str) -> tuple[int, str | None] | None:
    parts = item_id.split(":")
    if len(parts) not in (3, 4):
        return None

    prefix, encoded_type, tmdb_raw = parts[:3]
    if prefix != "tch" or encoded_type != media_type:
        return None

    imdb_id = parts[3] if len(parts) == 4 else None
    if imdb_id is not None and not re.fullmatch(r"tt\d+", imdb_id):
        return None

    if not re.fullmatch(r"\d+", tmdb_raw):
        return None

    tmdb_id = int(tmdb_raw)
    if tmdb_id <= 0:
        return None

    return tmdb_id, imdb_id


def stream_base_id(tmdb_id: int, imdb_id: str | None) -> str:
    """
    ID handed to stream addons: IMDb when known, otherwise `tmdb:<id>`, which
    AIOStreams (and other addons that list the `tmdb` prefix) resolve.
    """
    return imdb_id or f"tmdb:{tmdb_id}"


def catalog_meta_preview(
    media_type: str,
    item: dict[str, Any],
    imdb_id: str | None,
) -> dict[str, Any] | None:
    poster = image_url(item.get("poster_path"), "w500")
    if not poster:
        return None

    tmdb_id = int(item["id"])
    name = (
        item.get("title")
        or item.get("name")
        or item.get("original_title")
        or item.get("original_name")
        or "Untitled"
    )

    meta: dict[str, Any] = {
        "id": make_custom_id(media_type, tmdb_id, imdb_id),
        "type": media_type,
        "name": name,
        "poster": poster,
        "posterShape": "poster",
    }

    # For movies the detail meta will use this ID as the actual stream request ID.
    # Series carry their stream IDs in `videos`.
    if media_type == "movie":
        meta["behaviorHints"] = {"defaultVideoId": stream_base_id(tmdb_id, imdb_id)}

    if item.get("overview"):
        meta["description"] = item["overview"]

    date_value = item.get("release_date") or item.get("first_air_date")
    if date_value:
        meta["releaseInfo"] = date_value[:4]

    background = image_url(item.get("backdrop_path"), "original")
    if background:
        meta["background"] = background

    return meta


async def catalog_metas(
    media_type: str,
    tmdb_type: str,
    results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    results = [item for item in results if isinstance(item, dict) and item.get("id")]
    imdb_ids = await asyncio.gather(
        *(imdb_id_for_tmdb(tmdb_type, int(item["id"])) for item in results),
        return_exceptions=True,
    )

    metas: list[dict[str, Any]] = []
    for item, imdb_result in zip(results, imdb_ids):
        # A failed lookup is skipped rather than emitted as tmdb-only, so a transient
        # TMDB error never gives a title a second, different ID.
        if isinstance(imdb_result, Exception):
            continue

        meta = catalog_meta_preview(media_type, item, imdb_result)
        if meta:
            metas.append(meta)

    return metas


def catalog_language(cfg: dict[str, Any], config: AddonConfig | None = None) -> str:
    """TMDB `language` for a catalog row, honouring the install's script preference."""
    script = (config or current_config.get()).script
    if script == "hans":
        return "zh-CN"
    if script == "hant":
        return "zh-HK" if cfg["country"] == "HK" else "zh-TW"
    return cfg["language"]


def discover_genres(cfg: dict[str, Any], selection: str) -> str | None:
    """TMDB `with_genres` for a row plus an optional genre chosen in the dropdown."""
    row_genres = cfg.get("with_genres") or ""
    chosen = genre_map(cfg["tmdb_type"]).get(selection)
    if not chosen:
        return row_genres or None
    if not row_genres:
        return chosen
    if "|" in row_genres:
        # TMDB cannot mix AND with an OR list; those rows offer no genre dropdown.
        return row_genres
    return f"{row_genres},{chosen}"  # comma = AND


async def discover_catalog(
    media_type: str,
    catalog_id: str,
    skip: int,
    sort_mode: str = "Popular",
) -> dict[str, Any]:
    cfg = CATALOGS.get((media_type, catalog_id))

    if not cfg:
        return {"metas": []}

    # `sort_mode` is the dropdown value: a sort order, or a genre label (listed by popularity).
    selection = (
        sort_mode
        if sort_mode in SORT_OPTIONS or sort_mode in genre_map(cfg["tmdb_type"])
        else "Popular"
    )
    language = catalog_language(cfg)

    cache_key = f"catalog:{media_type}:{catalog_id}:{selection}:{skip}:{language}:v3"
    cached = await cache_get(cache_key)
    if cached is not None:
        return cached

    page = (skip // 20) + 1
    tmdb_type = cfg["tmdb_type"]

    params: dict[str, Any] = {
        "language": language,
        "with_origin_country": cfg["country"],
        "include_adult": "false",
        "page": page,
    }
    with_genres = discover_genres(cfg, selection)
    if with_genres:
        params["with_genres"] = with_genres
    params.update(
        discover_sort_params(tmdb_type, selection if selection in SORT_OPTIONS else "Popular")
    )

    data = await tmdb_get(f"/discover/{tmdb_type}", params)
    metas = await catalog_metas(media_type, tmdb_type, data.get("results") or [])

    payload = {"metas": metas}
    await cache_set(cache_key, payload, 6 * 60 * 60)
    return payload


CHINESE_ORIGINAL_LANGUAGES = {"zh", "cn"}  # TMDB uses "cn" for Cantonese
CHINESE_COUNTRIES = ("HK", "CN", "TW")  # priority when a title lists several


def origin_bucket(countries: set[str]) -> str | None:
    """Same HK > CN > TW choice as get_chinese_bundle, so a title lands in one catalog."""
    for country in CHINESE_COUNTRIES:
        if country in countries:
            return country
    return None


async def search_origin_bucket(tmdb_type: str, item: dict[str, Any]) -> str | None:
    listed = {
        str(code).upper()
        for code in (item.get("origin_country") or [])
        if isinstance(code, str)
    }
    if (
        not listed & set(CHINESE_COUNTRIES)
        and item.get("original_language") not in CHINESE_ORIGINAL_LANGUAGES
    ):
        return None

    # Movie search results carry no country; the en-US detail is also what the
    # meta handler fetches first, so this request is reused when the title opens.
    details = await get_details(tmdb_type, int(item["id"]), "en-US", False)
    return origin_bucket(country_codes(details))


async def search_catalog(
    media_type: str,
    catalog_id: str,
    query: str,
    skip: int = 0,
) -> dict[str, Any]:
    cfg = CATALOGS.get((media_type, catalog_id))
    if not cfg or not query:
        return {"metas": []}

    language = catalog_language(cfg)
    cache_key = f"search:{media_type}:{catalog_id}:{query}:{skip}:{language}"
    cached = await cache_get(cache_key)
    if cached is not None:
        return cached

    tmdb_type = cfg["tmdb_type"]
    data = await tmdb_get(
        f"/search/{tmdb_type}",
        {
            "query": query,
            "language": language,
            "include_adult": "false",
            "page": (skip // 20) + 1,
        },
    )
    results = [
        item
        for item in (data.get("results") or [])
        if isinstance(item, dict) and item.get("id")
    ]

    buckets = await asyncio.gather(
        *(search_origin_bucket(tmdb_type, item) for item in results),
        return_exceptions=True,
    )
    matching = [
        item
        for item, bucket in zip(results, buckets)
        if bucket == cfg["country"]
    ]

    payload = {"metas": await catalog_metas(media_type, tmdb_type, matching)}
    await cache_set(cache_key, payload, 60 * 60)
    return payload


async def find_tmdb_from_imdb(media_type: str, imdb_id: str) -> tuple[str, int] | None:
    cache_key = f"find:{media_type}:{imdb_id}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached or None

    data = await tmdb_get(
        f"/find/{imdb_id}",
        {"external_source": "imdb_id", "language": "en-US"},
    )

    result_key = "movie_results" if media_type == "movie" else "tv_results"
    results = data.get(result_key, [])

    if not results:
        cache.set(cache_key, "", 24 * 60 * 60)
        return None

    tmdb_type = "movie" if media_type == "movie" else "tv"
    result = (tmdb_type, int(results[0]["id"]))

    cache.set(cache_key, result, 7 * 24 * 60 * 60)
    return result


def country_codes(details: dict[str, Any]) -> set[str]:
    result: set[str] = set()

    for code in details.get("origin_country") or []:
        if isinstance(code, str):
            result.add(code.upper())

    for country in details.get("production_countries") or []:
        code = country.get("iso_3166_1") if isinstance(country, dict) else None
        if code:
            result.add(str(code).upper())

    return result


def locale_chain(country: str) -> list[str]:
    """Locale preference for a title's metadata, shaped by the install's script setting."""
    script = current_config.get().script
    if script == "hans":
        return ["zh-CN", "zh-TW", "zh-HK"]
    if script == "hant":
        return ["zh-HK", "zh-TW", "zh-CN"] if country == "HK" else ["zh-TW", "zh-HK", "zh-CN"]
    if country == "HK":
        return ["zh-HK", "zh-TW", "zh-CN"]
    if country == "TW":
        return ["zh-TW", "zh-HK", "zh-CN"]
    return ["zh-CN", "zh-TW", "zh-HK"]


async def get_details(
    tmdb_type: str,
    tmdb_id: int,
    locale: str,
    include_credits: bool = False,
) -> dict[str, Any]:
    cache_key = f"details:{tmdb_type}:{tmdb_id}:{locale}:{int(include_credits)}:v3"
    cached = await cache_get(cache_key)
    if cached is not None:
        return cached

    params: dict[str, Any] = {"language": locale}

    if include_credits:
        # The full (credits) request is the one the detail page uses, so fetch the
        # Chinese logos, posters and trailers in the same call.
        credits_part = "credits" if tmdb_type == "movie" else "aggregate_credits"
        params["append_to_response"] = f"{credits_part},images,videos"
        params["include_image_language"] = "zh"
        params["include_video_language"] = "zh"

    data = await tmdb_get(f"/{tmdb_type}/{tmdb_id}", params)
    await cache_set(cache_key, data, 24 * 60 * 60)
    return data


REGION_BY_LOCALE = {"zh-CN": "CN", "zh-TW": "TW", "zh-HK": "HK"}


def preferred_regions(locales: list[str]) -> list[str]:
    """TMDB region codes in locale order; Singapore (simplified) follows China."""
    regions: list[str] = []
    for locale in locales:
        region = REGION_BY_LOCALE.get(locale)
        if region and region not in regions:
            regions.append(region)
            if region == "CN":
                regions.append("SG")
    return regions


def region_rank(item: dict[str, Any], regions: list[str]) -> int:
    region = str(item.get("iso_3166_1") or "").upper()
    return regions.index(region) if region in regions else len(regions)


def pick_chinese_image(images: Any, kind: str, locales: list[str]) -> str | None:
    """
    Best Chinese-language image of `kind` ("logos" or "posters") from TMDB `images`:
    prefer the script region the locale chain favours (HK/TW traditional, CN
    simplified), then PNG/JPG over SVG, then TMDB's vote average.
    """
    if not isinstance(images, dict):
        return None

    regions = preferred_regions(locales)

    def rank(image: dict[str, Any]) -> tuple[int, int, float]:
        is_svg = str(image.get("file_path") or "").lower().endswith(".svg")
        return region_rank(image, regions), int(is_svg), -float(image.get("vote_average") or 0)

    candidates = [
        image
        for image in (images.get(kind) or [])
        if isinstance(image, dict)
        and image.get("iso_639_1") == "zh"
        and isinstance(image.get("file_path"), str)
    ]
    if not candidates:
        return None

    best = min(candidates, key=rank)
    if best["file_path"].lower().endswith(".svg"):
        return image_url(best["file_path"], "original")
    return image_url(best["file_path"], "w500")


def pick_chinese_logo(images: Any, country: str | None) -> str | None:
    return pick_chinese_image(images, "logos", locale_chain(country or "CN"))


def pick_chinese_trailers(videos: Any, locales: list[str]) -> list[dict[str, str]]:
    """
    Chinese YouTube trailers/teasers from TMDB `videos`, best first: Trailer before
    Teaser, preferred region, official uploads, then most recent.
    """
    results = videos.get("results") if isinstance(videos, dict) else None
    regions = preferred_regions(locales)

    def rank(video: dict[str, Any]) -> tuple[int, int, int]:
        return (
            0 if video.get("type") == "Trailer" else 1,
            region_rank(video, regions),
            0 if video.get("official") else 1,
        )

    candidates = [
        video
        for video in (results or [])
        if isinstance(video, dict)
        and video.get("iso_639_1") == "zh"
        and video.get("site") == "YouTube"
        and video.get("type") in {"Trailer", "Teaser"}
        and isinstance(video.get("key"), str)
        and re.fullmatch(r"[A-Za-z0-9_-]{6,20}", video["key"])
    ]

    # Newest first (ISO timestamps sort lexically); the stable sort below keeps
    # that order within equal ranks.
    candidates.sort(key=lambda video: str(video.get("published_at") or ""), reverse=True)

    seen: set[str] = set()
    trailers: list[dict[str, str]] = []
    for video in sorted(candidates, key=rank):
        if video["key"] in seen:
            continue
        seen.add(video["key"])
        trailers.append({"title": str(video.get("name") or "预告片"), "ytId": video["key"]})
    return trailers[:3]


def merge_localized_details(
    tmdb_type: str,
    preferred: dict[str, Any],
    fallbacks: list[dict[str, Any]],
    english: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(preferred or {})
    title_key = "title" if tmdb_type == "movie" else "name"
    original_key = "original_title" if tmdb_type == "movie" else "original_name"

    for field in (title_key, "overview"):
        if contains_cjk(merged.get(field)):
            continue

        localized = next(
            (item.get(field) for item in fallbacks if contains_cjk(item.get(field))),
            None,
        )
        if localized:
            merged[field] = localized
        elif not merged.get(field):
            fallback = next((item.get(field) for item in fallbacks if item.get(field)), None)
            if fallback:
                merged[field] = fallback

    if not merged.get(title_key):
        merged[title_key] = english.get(title_key) or english.get(original_key)

    return merged


async def get_chinese_bundle(
    tmdb_type: str,
    tmdb_id: int,
) -> tuple[dict[str, Any] | None, str | None, dict[str, Any] | None, list[str]]:
    """
    Returns:
      localized details, country (HK/CN/TW), embedded credits, locale preference chain.

    API reduction:
      - English detail determines origin.
      - Preferred Chinese detail appends credits in the same request.
      - Fallback locale details are requested only for fields without CJK text.
    """
    english = await get_details(tmdb_type, tmdb_id, "en-US", False)
    chosen_country = origin_bucket(country_codes(english))
    if not chosen_country:
        return None, None, None, []

    locales = locale_chain(chosen_country)
    preferred = await get_details(tmdb_type, tmdb_id, locales[0], True)

    fallbacks: list[dict[str, Any]] = []
    title_key = "title" if tmdb_type == "movie" else "name"

    if not contains_cjk(preferred.get(title_key)) or not contains_cjk(preferred.get("overview")):
        for locale in locales[1:]:
            item = await get_details(tmdb_type, tmdb_id, locale, False)
            fallbacks.append(item)

            have_title = contains_cjk(preferred.get(title_key)) or any(
                contains_cjk(x.get(title_key)) for x in fallbacks
            )
            have_overview = contains_cjk(preferred.get("overview")) or any(
                contains_cjk(x.get("overview")) for x in fallbacks
            )

            if have_title and have_overview:
                break

    merged = merge_localized_details(tmdb_type, preferred, fallbacks, english)

    credits_key = "credits" if tmdb_type == "movie" else "aggregate_credits"
    credits = preferred.get(credits_key) or {}

    return merged, chosen_country, credits, locales


def preferred_person_name(person: dict[str, Any]) -> str | None:
    original = person.get("original_name")
    name = person.get("name")

    if contains_cjk(original):
        return original

    if contains_cjk(name):
        return name

    return original or name


def pick_cjk_alias(aliases: list[Any]) -> str | None:
    candidates = [
        str(alias).strip()
        for alias in aliases
        if isinstance(alias, str) and contains_cjk(alias) and str(alias).strip()
    ]

    if not candidates:
        return None

    # Prefer a compact native name instead of long aliases containing annotations.
    candidates.sort(
        key=lambda value: (
            any(ch in value for ch in "()（）[]【】/／"),
            len(value),
        )
    )
    return candidates[0]


async def person_native_name(person: dict[str, Any]) -> str | None:
    immediate = preferred_person_name(person)

    if contains_cjk(immediate):
        return immediate

    person_id = person.get("id")
    if not person_id:
        return immediate

    cache_key = f"person-native-name:{person_id}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached or immediate

    try:
        # TMDB's language parameter does not reliably localize person names.
        # Person details can still contain native aliases in `also_known_as`.
        details = await tmdb_get(f"/person/{int(person_id)}")
        alias = pick_cjk_alias(details.get("also_known_as") or [])
        result = alias or immediate
    except Exception:
        result = immediate

    cache.set(cache_key, result or "", 30 * 24 * 60 * 60)
    return result


def select_credit_people(
    tmdb_type: str,
    credits: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    cast_rows = sorted(
        [row for row in (credits.get("cast") or []) if isinstance(row, dict)],
        key=lambda row: (
            row.get("order", 999999),
            -float(row.get("popularity") or 0),
        ),
    )[:15]

    directors: list[dict[str, Any]] = []
    writers: list[dict[str, Any]] = []

    for person in credits.get("crew") or []:
        if not isinstance(person, dict):
            continue

        department = str(person.get("department") or "")

        if tmdb_type == "movie":
            job_names = {str(person.get("job") or "")}
        else:
            job_names = {
                str(job.get("job") or "")
                for job in (person.get("jobs") or [])
                if isinstance(job, dict)
            }

        if "Director" in job_names and len(directors) < 5:
            if person.get("id") not in {x.get("id") for x in directors}:
                directors.append(person)

        if (
            job_names.intersection({"Writer", "Screenplay", "Story", "Teleplay", "Novel"})
            or department == "Writing"
        ) and len(writers) < 8:
            if person.get("id") not in {x.get("id") for x in writers}:
                writers.append(person)

    return cast_rows, directors, writers


async def names_for_people(people: list[dict[str, Any]]) -> list[str]:
    resolved = await asyncio.gather(
        *(person_native_name(person) for person in people),
        return_exceptions=True,
    )

    names: list[str] = []

    for person, result in zip(people, resolved):
        if isinstance(result, Exception):
            result = preferred_person_name(person)

        if result and result not in names:
            names.append(result)

    return names


async def localized_credit_fields(
    tmdb_type: str,
    credits: dict[str, Any],
) -> tuple[list[str], list[str], list[str]]:
    cast_people, director_people, writer_people = select_credit_people(tmdb_type, credits)

    cast, directors, writers = await asyncio.gather(
        names_for_people(cast_people),
        names_for_people(director_people),
        names_for_people(writer_people),
    )

    return cast, directors, writers


def search_link(name: str, category: str) -> dict[str, str]:
    return {
        "name": name,
        "category": category,
        "url": f"stremio:///search?search={quote(name, safe='')}",
    }


def overlay_localized_links(
    existing_links: list[dict[str, Any]] | None,
    cast: list[str],
    directors: list[str],
    writers: list[str],
    genres: list[str],
) -> list[dict[str, Any]]:
    # Cinemeta uses "Cast", "Directors", "Writers", "Genres"; ours are singular.
    # Drop an inherited English group only when we have a localized replacement.
    category_groups = {
        "actor": ({"actor", "actors", "cast"}, cast),
        "director": ({"director", "directors"}, directors),
        "writer": ({"writer", "writers"}, writers),
        "genre": ({"genre", "genres"}, genres),
    }
    replaced_categories = {
        alias
        for aliases, values in category_groups.values()
        if values
        for alias in aliases
    }

    links: list[dict[str, Any]] = [
        link
        for link in (existing_links or [])
        if isinstance(link, dict)
        and str(link.get("category") or "").strip().lower() not in replaced_categories
    ]

    links.extend(search_link(name, "actor") for name in cast)
    links.extend(search_link(name, "director") for name in directors)
    links.extend(search_link(name, "writer") for name in writers)
    links.extend(search_link(name, "genre") for name in genres)

    return links


async def get_season_details(
    tv_id: int,
    season_number: int,
    locale: str,
) -> dict[str, Any]:
    cache_key = season_cache_key(tv_id, season_number, locale)
    cached = await cache_get(cache_key)
    if cached is not None:
        return cached

    data = await tmdb_get(
        f"/tv/{tv_id}/season/{season_number}",
        {"language": locale},
    )
    await cache_set(cache_key, data, SEASON_TTL)
    return data


MAX_SEASONS = 60
SEASON_TTL = 24 * 60 * 60
APPEND_LIMIT = 20  # TMDB allows at most 20 appended sub-requests per call


def season_cache_key(tv_id: int, season_number: int, locale: str) -> str:
    return f"season:{tv_id}:{season_number}:{locale}"


async def prefetch_seasons(tv_id: int, numbers: list[int], locale: str) -> None:
    """
    Warm the per-season cache with one `/tv/{id}?append_to_response=season/1,...`
    call per 20 seasons instead of one request per season. Best effort: a failed
    chunk is skipped and `get_season_details` fetches those seasons individually.
    """
    cached = await asyncio.gather(
        *(cache_get(season_cache_key(tv_id, number, locale)) for number in numbers)
    )
    missing = [number for number, hit in zip(numbers, cached) if hit is None]
    if len(missing) < 2:  # a single season costs one request either way
        return

    async def fetch_chunk(chunk: list[int]) -> None:
        try:
            data = await tmdb_get(
                f"/tv/{tv_id}",
                {
                    "language": locale,
                    "append_to_response": ",".join(f"season/{number}" for number in chunk),
                },
            )
        except Exception:
            return

        for number in chunk:
            season = data.get(f"season/{number}")
            if isinstance(season, dict) and isinstance(season.get("episodes"), list):
                await cache_set(season_cache_key(tv_id, number, locale), season, SEASON_TTL)

    await asyncio.gather(
        *(
            fetch_chunk(missing[start : start + APPEND_LIMIT])
            for start in range(0, len(missing), APPEND_LIMIT)
        )
    )


def make_episode_id(series_id: str, season_number: int, episode_number: int) -> str:
    """`tt123:1:2` for IMDb titles, `tmdb:456:1:2` for titles without an IMDb ID."""
    if not re.fullmatch(r"tt\d+|tmdb:\d+", series_id):
        raise ValueError("A canonical IMDb or tmdb: series ID is required")
    if (
        type(season_number) is not int
        or type(episode_number) is not int
        or season_number <= 0
        or episode_number <= 0
    ):
        raise ValueError("Season and episode numbers must be positive")
    return f"{series_id}:{season_number}:{episode_number}"


def season_numbers_from_details(details: dict[str, Any]) -> list[int]:
    seasons = details.get("seasons") or []
    numbers = sorted(
        {
            season["season_number"]
            for season in seasons
            if isinstance(season, dict)
            and type(season.get("season_number")) is int
            and season["season_number"] > 0
        }
    )

    if not numbers:
        count = details.get("number_of_seasons")
        if type(count) is int and count > 0:
            numbers = list(range(1, count + 1))

    return numbers[:MAX_SEASONS]


async def localized_season(
    tv_id: int,
    season_number: int,
    locales: list[str],
) -> dict[str, Any]:
    episodes_by_number: dict[int, dict[str, Any]] = {}

    for locale in locales:
        data = await get_season_details(tv_id, season_number, locale)
        for episode in data.get("episodes") or []:
            if not isinstance(episode, dict):
                continue

            episode_number = episode.get("episode_number")
            if type(episode_number) is not int or episode_number <= 0:
                continue

            merged = episodes_by_number.setdefault(episode_number, dict(episode))
            for field in ("name", "overview"):
                if contains_cjk(episode.get(field)) and not contains_cjk(merged.get(field)):
                    merged[field] = episode[field]

            for field in ("air_date", "still_path", "runtime"):
                if not merged.get(field) and episode.get(field):
                    merged[field] = episode[field]

        rows = list(episodes_by_number.values())
        if rows and all(
            contains_cjk(episode.get("name"))
            and contains_cjk(episode.get("overview"))
            for episode in rows
        ):
            break

    return {"episodes": list(episodes_by_number.values())}


def normalize_video_aliases(video: dict[str, Any]) -> dict[str, Any]:
    """
    Stremio Core deserializes Video with `#[serde(alias = "name")] title` and
    `#[serde(alias = "stream")] streams`. A video carrying both spellings fails with
    a serde "duplicate field" error, which rejects the ENTIRE meta response and
    Stremio shows "No metadata was found!". Cinemeta videos use `name`, while we set
    a localized `title`, so collapse every alias pair to a single key.
    """
    name = video.pop("name", None)
    if not video.get("title") and isinstance(name, str) and name:
        video["title"] = name

    if "stream" in video:
        stream = video.pop("stream")
        if "streams" not in video and stream is not None:
            video["streams"] = stream

    return video


def build_series_videos(
    series_id: str,
    seasons_by_number: dict[int, dict[str, Any]],
    existing_videos: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    existing_by_episode: dict[tuple[int, int], dict[str, Any]] = {}
    for video in existing_videos:
        if not isinstance(video, dict):
            continue
        season_number = video.get("season")
        episode_number = video.get("episode")
        if (
            type(season_number) is int
            and season_number > 0
            and type(episode_number) is int
            and episode_number > 0
        ):
            existing_by_episode.setdefault((season_number, episode_number), video)

    season_numbers = sorted(
        number
        for number in seasons_by_number
        if type(number) is int and number > 0
    )[:MAX_SEASONS]
    if not season_numbers:
        season_numbers = sorted({season for season, _ in existing_by_episode})[:MAX_SEASONS]
    allowed_seasons = set(season_numbers)

    videos_by_episode: dict[tuple[int, int], dict[str, Any]] = {}
    for season_number in season_numbers:
        season = seasons_by_number.get(season_number) or {}
        for episode in season.get("episodes") or []:
            if not isinstance(episode, dict):
                continue

            episode_number = episode.get("episode_number")
            if type(episode_number) is not int or episode_number <= 0:
                continue

            key = (season_number, episode_number)
            video = normalize_video_aliases(dict(existing_by_episode.get(key) or {}))
            video.update(
                {
                    "id": make_episode_id(series_id, season_number, episode_number),
                    "season": season_number,
                    "episode": episode_number,
                }
            )

            localized_title = episode.get("name")
            if contains_cjk(localized_title):
                video["title"] = localized_title
            elif not video.get("title"):
                video["title"] = f"Episode {episode_number}"

            localized_overview = episode.get("overview")
            if contains_cjk(localized_overview):
                video["overview"] = localized_overview
            elif not video.get("overview") and localized_overview:
                video["overview"] = localized_overview

            air_date = episode.get("air_date")
            if air_date:
                video["released"] = f"{air_date}T00:00:00.000Z"
                video["releaseInfo"] = air_date[:4]

            thumbnail = image_url(episode.get("still_path"), "w500")
            if thumbnail:
                video["thumbnail"] = thumbnail

            runtime = episode.get("runtime")
            if runtime:
                video["runtime"] = f"{runtime} min"

            videos_by_episode[key] = video

    for key, existing in existing_by_episode.items():
        if key[0] not in allowed_seasons or key in videos_by_episode:
            continue
        video = normalize_video_aliases(dict(existing))
        video["id"] = make_episode_id(series_id, *key)
        if not video.get("title"):
            video["title"] = f"Episode {key[1]}"
        videos_by_episode[key] = video

    return [videos_by_episode[key] for key in sorted(videos_by_episode)]


async def localize_series_episodes(
    meta: dict[str, Any],
    tv_id: int,
    series_id: str,
    series_details: dict[str, Any],
    locales: list[str],
) -> None:
    season_numbers = season_numbers_from_details(series_details)
    # Long shows: fetch the preferred locale in bulk; the per-season calls below
    # then hit the cache. Fallback locales are only requested when needed.
    await prefetch_seasons(tv_id, season_numbers, locales[0])
    results = await asyncio.gather(
        *(localized_season(tv_id, number, locales) for number in season_numbers),
        return_exceptions=True,
    )

    seasons: dict[int, dict[str, Any]] = {}
    for number, result in zip(season_numbers, results):
        # Keep the season eligible so a temporary TMDB failure does not discard
        # matching canonical episodes already supplied by Cinemeta.
        seasons[number] = result if not isinstance(result, Exception) else {"episodes": []}

    existing_videos = meta.get("videos")
    if not isinstance(existing_videos, list):
        existing_videos = []

    meta["videos"] = build_series_videos(series_id, seasons, existing_videos)


def sanitize_meta_for_stremio(meta: dict[str, Any]) -> None:
    videos = meta.get("videos")
    if isinstance(videos, list):
        meta["videos"] = [
            normalize_video_aliases(dict(video))
            for video in videos
            if isinstance(video, dict)
        ]


def tmdb_fallback_meta(
    media_type: str,
    stream_id: str,
    details: dict[str, Any],
) -> dict[str, Any]:
    tmdb_type = "movie" if media_type == "movie" else "tv"
    title = details.get("title") if tmdb_type == "movie" else details.get("name")
    date_value = (
        details.get("release_date")
        if tmdb_type == "movie"
        else details.get("first_air_date")
    )

    meta: dict[str, Any] = {
        "id": stream_id,
        "type": media_type,
        "name": title or stream_id,
    }

    poster = image_url(details.get("poster_path"), "w500")
    background = image_url(details.get("backdrop_path"), "original")

    if poster:
        meta["poster"] = poster
        meta["posterShape"] = "poster"

    if background:
        meta["background"] = background

    if details.get("overview"):
        meta["description"] = details["overview"]

    if date_value:
        meta["releaseInfo"] = date_value[:4]
        meta["released"] = f"{date_value}T00:00:00.000Z"

    if tmdb_type == "movie" and details.get("runtime"):
        meta["runtime"] = f'{details["runtime"]} min'

    if details.get("genres"):
        meta["genres"] = [
            genre["name"]
            for genre in details["genres"]
            if isinstance(genre, dict) and genre.get("name")
        ]

    return meta


async def metadata_proxy(media_type: str, item_id: str) -> dict[str, Any]:
    """
    v1.4 uses our own `tch:` meta IDs.

    Why:
      - Stremio requests meta from every relevant addon.
      - With an IMDb `tt...` meta ID, Cinemeta usually appears before this addon and
        its English detail page becomes the selected meta item.
      - A custom `tch:` ID makes this addon the only relevant meta provider.

    Playback stays compatible:
      - movie -> behaviorHints.defaultVideoId = IMDb tt... ID (or tmdb:<id>)
      - series -> episode IDs are tt...:season:episode (or tmdb:<id>:season:episode)
    """
    if media_type not in {"movie", "series"}:
        return {"meta": None}

    parsed = parse_custom_id(media_type, item_id)
    if not parsed:
        return {"meta": None}

    tmdb_id, imdb_id = parsed
    stream_id = stream_base_id(tmdb_id, imdb_id)
    script = current_config.get().script
    cache_key = f"meta-custom:{media_type}:{item_id}:{script}:v160"
    cached = await cache_get(cache_key)
    if cached is not None:
        return cached

    tmdb_type = "movie" if media_type == "movie" else "tv"

    # Cinemeta is now only an internal data source. Stremio itself never asks
    # Cinemeta for our custom `tch:` meta ID.
    # Titles without an IMDb ID have nothing in Cinemeta.
    cine_task = (
        asyncio.create_task(cinemeta_meta(media_type, imdb_id)) if imdb_id else None
    )

    try:
        chinese, chosen_country, credits, locales = await get_chinese_bundle(
            tmdb_type,
            tmdb_id,
        )
    except Exception:
        chinese, chosen_country, credits, locales = None, None, None, []

    cine = await cine_task if cine_task else None

    # Catalogs only emit CN/HK entries, but keep a defensive fallback.
    if not chinese:
        try:
            english = await get_details(tmdb_type, tmdb_id, "en-US", False)
            meta = dict(cine or tmdb_fallback_meta(media_type, stream_id, english))
        except Exception:
            meta = dict(cine or {})

        if not meta:
            payload = {"meta": None}
            await cache_set(cache_key, payload, 30 * 60)
            return payload

        meta["id"] = item_id
        meta["type"] = media_type

        if media_type == "movie":
            hints = dict(meta.get("behaviorHints") or {})
            hints["defaultVideoId"] = stream_id
            meta["behaviorHints"] = hints

        sanitize_meta_for_stremio(meta)
        payload = {"meta": meta}
        await cache_set(cache_key, payload, 12 * 60 * 60)
        return payload

    # Start from Cinemeta internally so we retain useful IMDb metadata and,
    # for series, its canonical tt...:season:episode video IDs.
    meta = dict(cine or tmdb_fallback_meta(media_type, stream_id, chinese))
    title_key = "title" if tmdb_type == "movie" else "name"

    if chinese.get(title_key):
        meta["name"] = chinese[title_key]

    if chinese.get("overview"):
        meta["description"] = chinese["overview"]

    # Prefer a poster with Chinese title art; otherwise TMDB's default poster.
    poster = pick_chinese_image(chinese.get("images"), "posters", locales) or image_url(
        chinese.get("poster_path"), "w500"
    )
    background = image_url(chinese.get("backdrop_path"), "original")

    if poster:
        meta["poster"] = poster
        meta["posterShape"] = "poster"

    if background:
        meta["background"] = background

    # Cinemeta's logo is the English title art; prefer TMDB's Chinese logo, and
    # otherwise show no logo so Stremio displays the Chinese name as text.
    chinese_logo = pick_chinese_image(chinese.get("images"), "logos", locales)
    if chinese_logo:
        meta["logo"] = chinese_logo
    else:
        meta.pop("logo", None)

    # Chinese trailers replace Cinemeta's (usually English) ones; keep Cinemeta's
    # when TMDB has no Chinese YouTube trailer or teaser.
    trailers = pick_chinese_trailers(chinese.get("videos"), locales)
    if trailers:
        meta["trailerStreams"] = trailers
        meta["trailers"] = [{"source": t["ytId"], "type": "Trailer"} for t in trailers]

    localized_genres = [
        genre.get("name")
        for genre in (chinese.get("genres") or [])
        if isinstance(genre, dict) and genre.get("name")
    ]

    if localized_genres:
        meta["genres"] = localized_genres

    if credits:
        try:
            localized_cast, localized_directors, localized_writers = (
                await localized_credit_fields(tmdb_type, credits)
            )

            if localized_cast:
                meta["cast"] = localized_cast

            if localized_directors:
                meta["director"] = localized_directors
                meta["directors"] = localized_directors

            meta["links"] = overlay_localized_links(
                meta.get("links"),
                localized_cast,
                localized_directors,
                localized_writers,
                localized_genres,
            )
        except Exception:
            pass

    if media_type == "series" and locales:
        try:
            # Build the episode list from TMDB; preserve matching Cinemeta fields.
            await localize_series_episodes(meta, tmdb_id, stream_id, chinese, locales)
        except Exception:
            pass

    if chosen_country:
        meta["country"] = chosen_country

    # IMPORTANT: the meta ID stays custom so Cinemeta cannot take over the detail page.
    meta["id"] = item_id
    meta["type"] = media_type

    if media_type == "movie":
        # Stremio Core uses defaultVideoId when choosing the stream request ID.
        # This sends AIOStreams/TorBox the IMDb `tt...` ID (or `tmdb:<id>`).
        hints = dict(meta.get("behaviorHints") or {})
        hints["defaultVideoId"] = stream_id
        meta["behaviorHints"] = hints

        # Movies are single-video items. Do not let an inherited Cinemeta id become
        # the visible meta identity; stream identity is carried by defaultVideoId.
        meta.pop("videos", None)

    sanitize_meta_for_stremio(meta)
    payload = {"meta": meta}
    await cache_set(cache_key, payload, 12 * 60 * 60)
    return payload


NO_CACHE_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
}
CATALOG_HEADERS = {"Cache-Control": "public, max-age=1800"}


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    host = request.headers.get("host", "your-project.vercel.app")
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme or "https")
    manifest_url = f"{scheme}://{host}/manifest.json"
    configured = bool(TMDB_API_TOKEN or TMDB_API_KEY)

    status = "✅ TMDB configured" if configured else "⚠️ TMDB token not configured"
    rows = " · ".join(cfg["name"] for cfg in CATALOGS.values())

    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{APP_NAME}</title>
  <style>
    body{{font-family:system-ui,sans-serif;max-width:760px;margin:48px auto;padding:0 20px;line-height:1.55}}
    code{{background:#f3f3f3;padding:2px 6px;border-radius:5px;word-break:break-all}}
    a.button{{display:inline-block;padding:10px 16px;background:#5b45ff;color:#fff;text-decoration:none;border-radius:8px}}
    a.button.kofi{{background:#13c3ff;color:#102a43;margin-left:10px}}
    .muted{{opacity:.72;font-size:.92rem}}
  </style>
</head>
<body>
  <h1>{APP_NAME}</h1>
  <p>{rows}</p>
  <p>Popular · Latest · Top Rated · 按类型筛选 · 中文搜索</p>
  <p>CN/HK/TW: 中文详情、海报、预告片由本 addon 独立提供；播放仍使用 IMDb ID 交给 AIOStreams/TorBox。</p>
  <p>其他国家：保留 Cinemeta English metadata。</p>
  <p>{status}</p>
  <p><a class="button" href="/configure">Install in Stremio</a><a class="button kofi" href="{SUPPORT_URL}" target="_blank" rel="noopener">☕ 请我喝杯咖啡 · Support on Ko-fi</a></p>
  <p class="muted">这个 addon 免费且无广告；如果觉得好用，欢迎支持服务器与开发。</p>
  <p class="muted">Install 前需要先在设置页填写你自己的 TMDB API key，并选择简繁体与分类。
  Already installed from <code>{manifest_url}</code>? It keeps working unchanged.</p>
  <p>This addon provides catalogs and metadata only. It does not provide streams.</p>
  <p class="muted">This product uses the TMDB API but is not endorsed or certified by TMDB.</p>
</body>
</html>"""


CONFIGURE_PAGE = """<!doctype html>
<html lang="zh">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Configure · __NAME__</title>
  <style>
    body{font-family:system-ui,sans-serif;max-width:760px;margin:48px auto;padding:0 20px;line-height:1.55}
    code{background:#f3f3f3;padding:2px 6px;border-radius:5px;word-break:break-all}
    a.button,button{display:inline-block;padding:10px 16px;background:#5b45ff;color:#fff;text-decoration:none;border:0;border-radius:8px;font:inherit;cursor:pointer}
    button:disabled{opacity:.55;cursor:default}
    button.plain{background:#eee;color:#222;padding:4px 10px}
    input[type=text]{width:100%;box-sizing:border-box;padding:9px 10px;font:inherit;border:1px solid #bbb;border-radius:8px}
    label{display:block;margin:4px 0}
    .muted{opacity:.72;font-size:.92rem}
    .err{color:#b3261e}
    h2{margin:28px 0 8px;font-size:1.1rem}
  </style>
</head>
<body>
  <h1>Configure · 设置</h1>
  <p class="muted">__NAME__</p>

  <h2>1. TMDB API key（必填 · required）</h2>
  <p class="muted">Get a free key at
    <a href="https://www.themoviedb.org/settings/api" target="_blank" rel="noopener">themoviedb.org/settings/api</a>.
    Paste either the <b>API Key</b> (32 characters) or the <b>API Read Access Token</b> (starts with <code>eyJ</code>).
    It is checked directly with TMDB from your browser, and stored only inside your install link.</p>
  <input type="text" id="key" autocomplete="off" spellcheck="false" placeholder="TMDB API key or read access token">

  <h2>2. 文字 · Script</h2>
  <div id="scripts"></div>

  <h2>3. 分类 · Catalogs</h2>
  <div id="catalogs"></div>

  <p><button id="go">Generate install link</button></p>
  <p id="msg" class="err" role="alert"></p>

  <div id="out" hidden>
    <p><a class="button" id="install" href="#">Install in Stremio</a></p>
    <p>Or paste this URL into Stremio's addon search box:<br>
      <code id="url"></code> <button class="plain" id="copy" type="button">Copy</button></p>
    <p class="muted">The link contains your TMDB key. Keep it private.</p>
    <p class="muted">Enjoying it? <a href="__SUPPORT__" target="_blank" rel="noopener">☕ Support on Ko-fi</a></p>
  </div>

  <p class="muted"><a href="/">← Back</a> · <a href="__SUPPORT__" target="_blank" rel="noopener">☕ Ko-fi</a> · This product uses the TMDB API but is not endorsed or certified by TMDB.</p>

<script>
const STATE = __STATE__;
const $ = (id) => document.getElementById(id);
const V3 = /^[0-9a-f]{32}$/;
const V4 = /^eyJ[\\w-]+\\.[\\w-]+\\.[\\w-]+$/;

$("key").value = STATE.key || "";
for (const [id, label] of Object.entries(STATE.scripts)) {
  const row = document.createElement("label");
  row.innerHTML = '<input type="radio" name="script"> <span></span>';
  const input = row.querySelector("input");
  input.value = id;
  input.checked = id === STATE.script;
  row.querySelector("span").textContent = label;
  $("scripts").appendChild(row);
}
for (const catalog of STATE.catalogs) {
  const row = document.createElement("label");
  row.innerHTML = '<input type="checkbox" name="catalog"> <span></span>';
  const input = row.querySelector("input");
  input.value = catalog.id;
  input.checked = STATE.selected.includes(catalog.id);
  row.querySelector("span").textContent = catalog.name;
  $("catalogs").appendChild(row);
}

function b64url(text) {
  return btoa(text).replace(/\\+/g, "-").replace(/\\//g, "_").replace(/=+$/, "");
}

// "ok" | "bad" | "unreachable". Runs in the browser so the key never touches our server.
async function checkKey(key) {
  const headers = { accept: "application/json" };
  let url = "https://api.themoviedb.org/3/configuration";
  if (key.startsWith("eyJ")) headers.Authorization = "Bearer " + key;
  else url += "?api_key=" + encodeURIComponent(key);
  try {
    const response = await fetch(url, { headers });
    if (response.ok) return "ok";
    return response.status === 401 ? "bad" : "unreachable";
  } catch (error) {
    return "unreachable";
  }
}

function fail(text) {
  $("msg").textContent = text;
  $("out").hidden = true;
}

$("go").addEventListener("click", async () => {
  const key = $("key").value.trim();
  const script = document.querySelector('input[name="script"]:checked');
  const catalogs = [...document.querySelectorAll('input[name="catalog"]:checked')].map((x) => x.value);
  $("msg").textContent = "";
  $("out").hidden = true;

  if (!key) return fail("Enter your TMDB API key first. 请先填写 TMDB API key。");
  if (!V3.test(key) && !V4.test(key))
    return fail("That is not a TMDB key: use the 32-character API Key or the Read Access Token (eyJ…).");
  if (!script) return fail("Choose a script. 请选择文字。");
  if (!catalogs.length) return fail("Select at least one catalog. 至少选择一个分类。");

  $("go").disabled = true;
  $("go").textContent = "Checking key…";
  const result = await checkKey(key);
  $("go").disabled = false;
  $("go").textContent = "Generate install link";
  if (result === "bad") return fail("TMDB rejected this key. Check it and try again. TMDB 不接受此 key。");
  if (result === "unreachable") return fail("Could not verify the key with TMDB right now. Try again in a moment.");

  const config = b64url(JSON.stringify({ s: script.value, c: catalogs, k: key }));
  const base = location.host + "/" + config + "/manifest.json";
  $("install").href = "stremio://" + base;
  $("url").textContent = location.protocol + "//" + base;
  $("out").hidden = false;
});

$("copy").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText($("url").textContent);
    $("copy").textContent = "Copied";
  } catch (error) {
    $("copy").textContent = "Select the URL and copy it";
  }
});
</script>
</body>
</html>"""


def render_configure_page(config: AddonConfig) -> str:
    state = {
        "script": config.script,
        "selected": list(config.catalogs),
        "key": config.tmdb_key,
        "scripts": SCRIPTS,
        "catalogs": [
            {"id": catalog_id, "name": cfg["name"]}
            for (_, catalog_id), cfg in CATALOGS.items()
        ],
    }
    # Inside <script>: keep the JSON from ever closing the tag or starting a comment.
    payload = (
        json.dumps(state, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    return (
        CONFIGURE_PAGE.replace("__NAME__", APP_NAME)
        .replace("__SUPPORT__", SUPPORT_URL)
        .replace("__STATE__", payload)  # last: the JSON must never be re-scanned
    )


def config_from_segment(segment: str) -> AddonConfig:
    config = decode_config(segment)
    if config is None:
        raise HTTPException(status_code=404, detail="Invalid addon configuration")
    return config


async def call_with_config(config: AddonConfig, handler, *args):
    token = current_config.set(config)
    try:
        return await handler(*args)
    finally:
        current_config.reset(token)


@app.get("/configure", response_class=HTMLResponse)
async def configure():
    return render_configure_page(DEFAULT_CONFIG)


@app.get("/manifest.json")
async def manifest():
    return JSONResponse(MANIFEST, headers=NO_CACHE_HEADERS)


@app.get("/v140/manifest.json")
async def manifest_v140():
    return JSONResponse(MANIFEST, headers=NO_CACHE_HEADERS)


@app.get("/catalog/{media_type}/{catalog_id}.json")
async def catalog(media_type: str, catalog_id: str):
    try:
        payload = await discover_catalog(media_type, catalog_id, 0, "Popular")
        return JSONResponse(payload, headers=CATALOG_HEADERS)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"TMDB error: {exc.response.status_code}",
        ) from exc


@app.get("/catalog/{media_type}/{catalog_id}/{extra_args}.json")
async def catalog_with_extra(
    media_type: str,
    catalog_id: str,
    extra_args: str,
):
    try:
        skip, selection = parse_extra(extra_args)
        query = parse_search(extra_args)
        if query:
            payload = await search_catalog(media_type, catalog_id, query, skip)
        else:
            payload = await discover_catalog(
                media_type,
                catalog_id,
                skip,
                selection,
            )
        return JSONResponse(payload, headers=CATALOG_HEADERS)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"TMDB error: {exc.response.status_code}",
        ) from exc


@app.get("/meta/{media_type}/{item_id}.json")
async def meta(media_type: str, item_id: str):
    try:
        payload = await metadata_proxy(media_type, item_id)
        return JSONResponse(payload, headers=CATALOG_HEADERS)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Upstream metadata error: {exc.response.status_code}",
        ) from exc


@app.get("/v140/catalog/{media_type}/{catalog_id}.json")
async def catalog_v140(media_type: str, catalog_id: str):
    return await catalog(media_type, catalog_id)


@app.get("/v140/catalog/{media_type}/{catalog_id}/{extra_args}.json")
async def catalog_with_extra_v140(
    media_type: str,
    catalog_id: str,
    extra_args: str,
):
    return await catalog_with_extra(media_type, catalog_id, extra_args)


@app.get("/v140/meta/{media_type}/{item_id}.json")
async def meta_v140(media_type: str, item_id: str):
    return await meta(media_type, item_id)


# Configured installs: /{config}/... where {config} is AddonConfig.encode().
# Registered after the /v140/... routes so those literal prefixes still win.
@app.get("/{config}/configure", response_class=HTMLResponse)
async def configure_existing(config: str):
    return render_configure_page(config_from_segment(config))


@app.get("/{config}/manifest.json")
async def manifest_configured(config: str):
    return JSONResponse(build_manifest(config_from_segment(config)), headers=NO_CACHE_HEADERS)


@app.get("/{config}/catalog/{media_type}/{catalog_id}.json")
async def catalog_configured(config: str, media_type: str, catalog_id: str):
    parsed = config_from_segment(config)
    if catalog_id not in parsed.catalogs:
        return JSONResponse({"metas": []}, headers=CATALOG_HEADERS)
    return await call_with_config(parsed, catalog, media_type, catalog_id)


@app.get("/{config}/catalog/{media_type}/{catalog_id}/{extra_args}.json")
async def catalog_with_extra_configured(
    config: str,
    media_type: str,
    catalog_id: str,
    extra_args: str,
):
    parsed = config_from_segment(config)
    if catalog_id not in parsed.catalogs:
        return JSONResponse({"metas": []}, headers=CATALOG_HEADERS)
    return await call_with_config(
        parsed, catalog_with_extra, media_type, catalog_id, extra_args
    )


@app.get("/{config}/meta/{media_type}/{item_id}.json")
async def meta_configured(config: str, media_type: str, item_id: str):
    return await call_with_config(config_from_segment(config), meta, media_type, item_id)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "tmdbConfigured": bool(TMDB_API_TOKEN or TMDB_API_KEY),
        "sharedCache": bool(REDIS_URL and REDIS_TOKEN),
        "version": APP_VERSION,
        "supportUrl": SUPPORT_URL,
        "features": [
            "CN/HK/TW Chinese metadata",
            "native-name credit aliases",
            "localized TV episodes (up to 60 seasons)",
            "Popular/Latest/Top Rated + genre filter",
            "custom meta IDs with IMDb stream IDs",
            "tmdb: stream IDs for titles without IMDb",
            "Chinese title search",
            "Chinese title logos, posters and trailers",
            "per-install settings (script, catalogs, TMDB key)",
            "optional shared Redis cache",
        ],
        "sortOptions": SORT_OPTIONS,
        "catalogs": [cfg["name"] for cfg in CATALOGS.values()],
    }
