import asyncio
import os
import time
from typing import Any
from urllib.parse import unquote

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

APP_NAME = "Chinese & HK Catalog"
APP_VERSION = "1.0.0"

TMDB_API_TOKEN = os.getenv("TMDB_API_TOKEN", "").strip()
TMDB_API_KEY = os.getenv("TMDB_API_KEY", "").strip()

TMDB_BASE = "https://api.themoviedb.org/3"
TMDB_IMAGE = "https://image.tmdb.org/t/p"
CINEMETA_BASE = "https://v3-cinemeta.strem.io"

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
}

MANIFEST = {
    "id": "com.twin.chinese-hk-catalog",
    "version": APP_VERSION,
    "name": APP_NAME,
    "description": (
        "China/Hong Kong movie and series catalogs with Chinese titles/descriptions. "
        "Other IMDb metadata falls back to Cinemeta in English."
    ),
    "resources": [
        "catalog",
        {"name": "meta", "types": ["movie", "series"], "idPrefixes": ["tt"]},
    ],
    "types": ["movie", "series"],
    "catalogs": [
        {
            "type": media_type,
            "id": catalog_id,
            "name": cfg["name"],
            "extra": [{"name": "skip", "isRequired": False}],
        }
        for (media_type, catalog_id), cfg in CATALOGS.items()
    ],
    "idPrefixes": ["tt"],
    "behaviorHints": {"adult": False, "p2p": False},
}


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

app = FastAPI(title=APP_NAME, version=APP_VERSION)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
)


def require_tmdb_credentials() -> None:
    if not TMDB_API_TOKEN and not TMDB_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="TMDB is not configured. Set TMDB_API_TOKEN (recommended) or TMDB_API_KEY in Vercel.",
        )


async def tmdb_get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    require_tmdb_credentials()
    params = dict(params or {})
    headers = {"accept": "application/json"}
    if TMDB_API_TOKEN:
        headers["Authorization"] = f"Bearer {TMDB_API_TOKEN}"
    else:
        params["api_key"] = TMDB_API_KEY

    async with tmdb_sem:
        response = await http.get(f"{TMDB_BASE}{path}", params=params, headers=headers)
    if response.status_code == 404:
        return {}
    response.raise_for_status()
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


def parse_skip(extra_args: str | None) -> int:
    if not extra_args:
        return 0
    decoded = unquote(extra_args.removesuffix(".json"))
    for part in decoded.replace("&", "/").split("/"):
        if part.startswith("skip="):
            try:
                return max(0, int(part.split("=", 1)[1]))
            except ValueError:
                return 0
    return 0


async def imdb_id_for_tmdb(tmdb_type: str, tmdb_id: int) -> str | None:
    key = f"external:{tmdb_type}:{tmdb_id}"
    cached = cache.get(key)
    if cached is not None:
        return cached or None

    data = await tmdb_get(f"/{tmdb_type}/{tmdb_id}/external_ids")
    imdb_id = data.get("imdb_id")
    if not isinstance(imdb_id, str) or not imdb_id.startswith("tt"):
        imdb_id = None

    cache.set(key, imdb_id or "", 7 * 24 * 60 * 60)
    return imdb_id


async def discover_catalog(media_type: str, catalog_id: str, skip: int) -> dict[str, Any]:
    cfg = CATALOGS.get((media_type, catalog_id))
    if not cfg:
        return {"metas": []}

    cache_key = f"catalog:{media_type}:{catalog_id}:{skip}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    # TMDB pages are 20 items. Stremio normally advances skip by the returned page size.
    page = (skip // 20) + 1
    tmdb_type = cfg["tmdb_type"]
    data = await tmdb_get(
        f"/discover/{tmdb_type}",
        {
            "language": cfg["language"],
            "with_origin_country": cfg["country"],
            "sort_by": "popularity.desc",
            "include_adult": "false",
            "page": page,
        },
    )

    results = data.get("results", [])
    imdb_ids = await asyncio.gather(
        *(imdb_id_for_tmdb(tmdb_type, int(item["id"])) for item in results),
        return_exceptions=True,
    )

    metas = []
    for item, imdb_result in zip(results, imdb_ids):
        if isinstance(imdb_result, Exception) or not imdb_result:
            # Keep only IMDb-backed entries so AIOStreams/TorBox can receive the normal tt... ID.
            continue

        name = (
            item.get("title")
            or item.get("name")
            or item.get("original_title")
            or item.get("original_name")
            or "Untitled"
        )
        poster = image_url(item.get("poster_path"), "w500")
        if not poster:
            continue

        meta = {
            "id": imdb_result,
            "type": media_type,
            "name": name,
            "poster": poster,
            "posterShape": "poster",
        }
        if item.get("overview"):
            meta["description"] = item["overview"]
        date = item.get("release_date") or item.get("first_air_date")
        if date:
            meta["releaseInfo"] = date[:4]
        backdrop = image_url(item.get("backdrop_path"), "original")
        if backdrop:
            meta["background"] = backdrop
        metas.append(meta)

    payload = {"metas": metas}
    cache.set(cache_key, payload, 6 * 60 * 60)
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
    result = set()
    for code in details.get("origin_country") or []:
        if isinstance(code, str):
            result.add(code.upper())
    for country in details.get("production_countries") or []:
        code = country.get("iso_3166_1") if isinstance(country, dict) else None
        if code:
            result.add(str(code).upper())
    return result


async def get_localized_details(tmdb_type: str, tmdb_id: int, locale: str) -> dict[str, Any]:
    key = f"details:{tmdb_type}:{tmdb_id}:{locale}"
    cached = cache.get(key)
    if cached is not None:
        return cached

    data = await tmdb_get(
        f"/{tmdb_type}/{tmdb_id}",
        {"language": locale},
    )
    cache.set(key, data, 24 * 60 * 60)
    return data


async def get_chinese_details(tmdb_type: str, tmdb_id: int) -> tuple[dict[str, Any] | None, str | None]:
    english = await get_localized_details(tmdb_type, tmdb_id, "en-US")
    countries = country_codes(english)

    if "HK" in countries:
        locales = ["zh-HK", "zh-TW", "zh-CN"]
        chosen_country = "HK"
    elif "CN" in countries:
        locales = ["zh-CN", "zh-TW", "zh-HK"]
        chosen_country = "CN"
    else:
        return None, None

    localized_versions = []
    for locale in locales:
        localized_versions.append(await get_localized_details(tmdb_type, tmdb_id, locale))

    # Prefer the first locale, but fill missing title/overview from Chinese fallbacks.
    merged = dict(localized_versions[0] or {})
    title_key = "title" if tmdb_type == "movie" else "name"
    original_key = "original_title" if tmdb_type == "movie" else "original_name"

    if not merged.get(title_key):
        for item in localized_versions[1:]:
            if item.get(title_key):
                merged[title_key] = item[title_key]
                break
    if not merged.get("overview"):
        for item in localized_versions[1:]:
            if item.get("overview"):
                merged["overview"] = item["overview"]
                break
    if not merged.get(title_key):
        merged[title_key] = english.get(title_key) or english.get(original_key)

    return merged, chosen_country


def tmdb_fallback_meta(media_type: str, imdb_id: str, details: dict[str, Any]) -> dict[str, Any]:
    tmdb_type = "movie" if media_type == "movie" else "tv"
    title = details.get("title") if tmdb_type == "movie" else details.get("name")
    date = details.get("release_date") if tmdb_type == "movie" else details.get("first_air_date")

    meta: dict[str, Any] = {
        "id": imdb_id,
        "type": media_type,
        "name": title or imdb_id,
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
    if date:
        meta["releaseInfo"] = date[:4]
        meta["released"] = f"{date}T00:00:00.000Z"
    if tmdb_type == "movie" and details.get("runtime"):
        meta["runtime"] = f'{details["runtime"]} min'
    if details.get("genres"):
        meta["genres"] = [g["name"] for g in details["genres"] if g.get("name")]
    return meta


async def metadata_proxy(media_type: str, imdb_id: str) -> dict[str, Any]:
    if media_type not in {"movie", "series"} or not imdb_id.startswith("tt"):
        return {"meta": None}

    cache_key = f"meta-proxy:{media_type}:{imdb_id}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    # Cinemeta provides the canonical IMDb-compatible video/episode IDs used by stream addons.
    cine_task = asyncio.create_task(cinemeta_meta(media_type, imdb_id))

    try:
        mapping = await find_tmdb_from_imdb(media_type, imdb_id)
    except Exception:
        mapping = None

    cine = await cine_task

    if not mapping:
        payload = {"meta": cine} if cine else {"meta": None}
        cache.set(cache_key, payload, 12 * 60 * 60)
        return payload

    tmdb_type, tmdb_id = mapping

    try:
        chinese, chosen_country = await get_chinese_details(tmdb_type, tmdb_id)
    except Exception:
        chinese, chosen_country = None, None

    # Non-CN/HK title: preserve Cinemeta English exactly.
    if not chinese:
        if cine:
            payload = {"meta": cine}
        else:
            try:
                english = await get_localized_details(tmdb_type, tmdb_id, "en-US")
                payload = {"meta": tmdb_fallback_meta(media_type, imdb_id, english)}
            except Exception:
                payload = {"meta": None}
        cache.set(cache_key, payload, 12 * 60 * 60)
        return payload

    # CN/HK title: start from Cinemeta so series episode/video IDs remain compatible,
    # then overlay only the localized fields we actually want in Chinese.
    meta = dict(cine or tmdb_fallback_meta(media_type, imdb_id, chinese))
    title_key = "title" if tmdb_type == "movie" else "name"
    if chinese.get(title_key):
        meta["name"] = chinese[title_key]
    if chinese.get("overview"):
        meta["description"] = chinese["overview"]

    poster = image_url(chinese.get("poster_path"), "w500")
    background = image_url(chinese.get("backdrop_path"), "original")
    if poster:
        meta["poster"] = poster
        meta["posterShape"] = "poster"
    if background:
        meta["background"] = background

    if chosen_country:
        meta["country"] = chosen_country

    # Preserve the IMDb ID regardless of what the upstream metadata provider returns.
    meta["id"] = imdb_id
    meta["type"] = media_type

    payload = {"meta": meta}
    cache.set(cache_key, payload, 12 * 60 * 60)
    return payload


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    host = request.headers.get("host", "your-project.vercel.app")
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme or "https")
    manifest_url = f"{scheme}://{host}/manifest.json"
    stremio_url = f"stremio://{host}/manifest.json"
    configured = bool(TMDB_API_TOKEN or TMDB_API_KEY)
    status = "✅ TMDB configured" if configured else "⚠️ TMDB token not configured"

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
  </style>
</head>
<body>
  <h1>{APP_NAME}</h1>
  <p>🇨🇳 中国电影 · 🇭🇰 香港电影 · 🇨🇳 中国电视剧 · 🇭🇰 香港电视剧</p>
  <p>{status}</p>
  <p><a class="button" href="{stremio_url}">Install in Stremio</a></p>
  <p>Manifest: <code>{manifest_url}</code></p>
  <p>This addon provides catalogs and metadata only. It does not provide streams.</p>
</body>
</html>"""


@app.get("/manifest.json")
async def manifest():
    return JSONResponse(MANIFEST, headers={"Cache-Control": "public, max-age=3600"})


@app.get("/catalog/{media_type}/{catalog_id}.json")
async def catalog(media_type: str, catalog_id: str):
    try:
        payload = await discover_catalog(media_type, catalog_id, 0)
        return JSONResponse(payload, headers={"Cache-Control": "public, max-age=1800"})
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"TMDB error: {exc.response.status_code}") from exc


@app.get("/catalog/{media_type}/{catalog_id}/{extra_args}.json")
async def catalog_with_extra(media_type: str, catalog_id: str, extra_args: str):
    try:
        payload = await discover_catalog(media_type, catalog_id, parse_skip(extra_args))
        return JSONResponse(payload, headers={"Cache-Control": "public, max-age=1800"})
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"TMDB error: {exc.response.status_code}") from exc


@app.get("/meta/{media_type}/{imdb_id}.json")
async def meta(media_type: str, imdb_id: str):
    try:
        payload = await metadata_proxy(media_type, imdb_id)
        return JSONResponse(payload, headers={"Cache-Control": "public, max-age=1800"})
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"Upstream metadata error: {exc.response.status_code}") from exc


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "tmdbConfigured": bool(TMDB_API_TOKEN or TMDB_API_KEY),
        "version": APP_VERSION,
    }
