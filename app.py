import asyncio
import os
import re
import time
from datetime import date
from typing import Any
from urllib.parse import quote, unquote

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

APP_NAME = "Chinese & HK Catalog"
APP_VERSION = "1.3.1"

TMDB_API_TOKEN = os.getenv("TMDB_API_TOKEN", "").strip()
TMDB_API_KEY = os.getenv("TMDB_API_KEY", "").strip()

TMDB_BASE = "https://api.themoviedb.org/3"
TMDB_IMAGE = "https://image.tmdb.org/t/p"
CINEMETA_BASE = "https://v3-cinemeta.strem.io"

SORT_OPTIONS = ["Popular", "Latest", "Top Rated"]

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
    "id": "com.twin.chinese-hk-catalog.v13",
    "version": APP_VERSION,
    "name": APP_NAME,
    "description": (
        "China/Hong Kong movie and TV catalogs with Chinese metadata, localized credits "
        "where TMDB has native names, and Chinese episode titles/descriptions. "
        "Other IMDb titles remain in Cinemeta English."
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
            "extra": [
                {
                    "name": "genre",
                    "isRequired": False,
                    "options": SORT_OPTIONS,
                    "optionsLimit": 1,
                },
                {"name": "skip", "isRequired": False},
            ],
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


def contains_cjk(text: str | None) -> bool:
    if not text:
        return False
    return bool(re.search(r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]", text))


def parse_extra(extra_args: str | None) -> tuple[int, str]:
    skip = 0
    sort_mode = "Popular"

    if not extra_args:
        return skip, sort_mode

    decoded = unquote(extra_args.removesuffix(".json"))
    for part in decoded.replace("&", "/").split("/"):
        if "=" not in part:
            continue

        key, value = part.split("=", 1)

        if key == "skip":
            try:
                skip = max(0, int(value))
            except ValueError:
                skip = 0
        elif key == "genre" and value in SORT_OPTIONS:
            sort_mode = value

    return skip, sort_mode


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
    cached = cache.get(cache_key)
    if cached is not None:
        return cached or None

    data = await tmdb_get(f"/{tmdb_type}/{tmdb_id}/external_ids")
    imdb_id = data.get("imdb_id")

    if not isinstance(imdb_id, str) or not imdb_id.startswith("tt"):
        imdb_id = None

    cache.set(cache_key, imdb_id or "", 7 * 24 * 60 * 60)
    return imdb_id


async def discover_catalog(
    media_type: str,
    catalog_id: str,
    skip: int,
    sort_mode: str = "Popular",
) -> dict[str, Any]:
    cfg = CATALOGS.get((media_type, catalog_id))

    if not cfg:
        return {"metas": []}

    if sort_mode not in SORT_OPTIONS:
        sort_mode = "Popular"

    cache_key = f"catalog:{media_type}:{catalog_id}:{sort_mode}:{skip}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    page = (skip // 20) + 1
    tmdb_type = cfg["tmdb_type"]

    params: dict[str, Any] = {
        "language": cfg["language"],
        "with_origin_country": cfg["country"],
        "include_adult": "false",
        "page": page,
    }
    params.update(discover_sort_params(tmdb_type, sort_mode))

    data = await tmdb_get(f"/discover/{tmdb_type}", params)
    results = data.get("results", [])

    imdb_ids = await asyncio.gather(
        *(imdb_id_for_tmdb(tmdb_type, int(item["id"])) for item in results),
        return_exceptions=True,
    )

    metas: list[dict[str, Any]] = []

    for item, imdb_result in zip(results, imdb_ids):
        if isinstance(imdb_result, Exception) or not imdb_result:
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

        meta: dict[str, Any] = {
            "id": imdb_result,
            "type": media_type,
            "name": name,
            "poster": poster,
            "posterShape": "poster",
        }

        if item.get("overview"):
            meta["description"] = item["overview"]

        date_value = item.get("release_date") or item.get("first_air_date")
        if date_value:
            meta["releaseInfo"] = date_value[:4]

        background = image_url(item.get("backdrop_path"), "original")
        if background:
            meta["background"] = background

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
    if country == "HK":
        return ["zh-HK", "zh-TW", "zh-CN"]
    return ["zh-CN", "zh-TW", "zh-HK"]


async def get_details(
    tmdb_type: str,
    tmdb_id: int,
    locale: str,
    include_credits: bool = False,
) -> dict[str, Any]:
    cache_key = f"details:{tmdb_type}:{tmdb_id}:{locale}:{int(include_credits)}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    params: dict[str, Any] = {"language": locale}

    if include_credits:
        params["append_to_response"] = (
            "credits" if tmdb_type == "movie" else "aggregate_credits"
        )

    data = await tmdb_get(f"/{tmdb_type}/{tmdb_id}", params)
    cache.set(cache_key, data, 24 * 60 * 60)
    return data


def merge_localized_details(
    tmdb_type: str,
    preferred: dict[str, Any],
    fallbacks: list[dict[str, Any]],
    english: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(preferred or {})
    title_key = "title" if tmdb_type == "movie" else "name"
    original_key = "original_title" if tmdb_type == "movie" else "original_name"

    if not merged.get(title_key):
        for item in fallbacks:
            if item.get(title_key):
                merged[title_key] = item[title_key]
                break

    if not merged.get("overview"):
        for item in fallbacks:
            if item.get("overview"):
                merged["overview"] = item["overview"]
                break

    if not merged.get(title_key):
        merged[title_key] = english.get(title_key) or english.get(original_key)

    return merged


async def get_chinese_bundle(
    tmdb_type: str,
    tmdb_id: int,
) -> tuple[dict[str, Any] | None, str | None, dict[str, Any] | None, list[str]]:
    """
    Returns:
      localized details, country (CN/HK), embedded credits, locale preference chain.

    API reduction in v1.3:
      - English detail determines origin.
      - Preferred Chinese detail appends credits in the same request.
      - Fallback locale details are requested only if title/overview is missing.
    """
    english = await get_details(tmdb_type, tmdb_id, "en-US", False)
    countries = country_codes(english)

    if "HK" in countries:
        chosen_country = "HK"
    elif "CN" in countries:
        chosen_country = "CN"
    else:
        return None, None, None, []

    locales = locale_chain(chosen_country)
    preferred = await get_details(tmdb_type, tmdb_id, locales[0], True)

    fallbacks: list[dict[str, Any]] = []
    title_key = "title" if tmdb_type == "movie" else "name"

    if not preferred.get(title_key) or not preferred.get("overview"):
        for locale in locales[1:]:
            item = await get_details(tmdb_type, tmdb_id, locale, False)
            fallbacks.append(item)

            have_title = preferred.get(title_key) or any(x.get(title_key) for x in fallbacks)
            have_overview = preferred.get("overview") or any(x.get("overview") for x in fallbacks)

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
    replaced_categories = {"actor", "director", "writer", "genre"}

    links: list[dict[str, Any]] = [
        link
        for link in (existing_links or [])
        if isinstance(link, dict)
        and str(link.get("category") or "").lower() not in replaced_categories
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
    cache_key = f"season:{tv_id}:{season_number}:{locale}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    data = await tmdb_get(
        f"/tv/{tv_id}/season/{season_number}",
        {"language": locale},
    )
    cache.set(cache_key, data, 24 * 60 * 60)
    return data


def season_has_useful_localization(season: dict[str, Any]) -> bool:
    for episode in season.get("episodes") or []:
        if not isinstance(episode, dict):
            continue
        if contains_cjk(episode.get("name")) or contains_cjk(episode.get("overview")):
            return True
    return False


async def localized_season(
    tv_id: int,
    season_number: int,
    locales: list[str],
) -> dict[str, Any]:
    """
    Try the preferred Chinese locale first.
    Only query fallbacks when the season has no useful Chinese episode text.
    """
    for locale in locales:
        data = await get_season_details(tv_id, season_number, locale)
        if season_has_useful_localization(data):
            return data

    # Return the preferred locale even if TMDB has no Chinese episode translation,
    # allowing the caller to preserve Cinemeta text for missing fields.
    return await get_season_details(tv_id, season_number, locales[0])


def apply_localized_seasons_to_videos(
    videos: list[dict[str, Any]],
    seasons_by_number: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    episode_lookup: dict[tuple[int, int], dict[str, Any]] = {}

    for season_number, season in seasons_by_number.items():
        for episode in season.get("episodes") or []:
            if not isinstance(episode, dict):
                continue

            episode_number = episode.get("episode_number")
            if isinstance(episode_number, int):
                episode_lookup[(season_number, episode_number)] = episode

    updated: list[dict[str, Any]] = []

    for video in videos:
        item = dict(video)

        season_number = item.get("season")
        episode_number = item.get("episode")

        if not isinstance(season_number, int) or not isinstance(episode_number, int):
            updated.append(item)
            continue

        localized = episode_lookup.get((season_number, episode_number))
        if not localized:
            updated.append(item)
            continue

        # Never alter `id`; Cinemeta's tt...:season:episode ID is what stream addons use.
        if localized.get("name"):
            item["title"] = localized["name"]

        if localized.get("overview"):
            item["overview"] = localized["overview"]

        still = image_url(localized.get("still_path"), "w500")
        if still:
            item["thumbnail"] = still

        air_date = localized.get("air_date")
        if air_date:
            item["released"] = f"{air_date}T00:00:00.000Z"
            item["releaseInfo"] = air_date[:4]

        runtime = localized.get("runtime")
        if runtime:
            item["runtime"] = f"{runtime} min"

        updated.append(item)

    return updated


async def localize_series_episodes(
    meta: dict[str, Any],
    tv_id: int,
    locales: list[str],
) -> None:
    videos = meta.get("videos")

    if not isinstance(videos, list) or not videos:
        return

    season_numbers = sorted(
        {
            video.get("season")
            for video in videos
            if isinstance(video, dict) and isinstance(video.get("season"), int)
        }
    )

    # Defensive bound for extremely long-running shows on serverless hosting.
    season_numbers = season_numbers[:20]

    results = await asyncio.gather(
        *(localized_season(tv_id, number, locales) for number in season_numbers),
        return_exceptions=True,
    )

    seasons: dict[int, dict[str, Any]] = {}

    for number, result in zip(season_numbers, results):
        if isinstance(result, Exception):
            continue
        seasons[number] = result

    if seasons:
        meta["videos"] = apply_localized_seasons_to_videos(videos, seasons)


def tmdb_fallback_meta(
    media_type: str,
    imdb_id: str,
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


async def metadata_proxy(media_type: str, imdb_id: str) -> dict[str, Any]:
    if media_type not in {"movie", "series"} or not imdb_id.startswith("tt"):
        return {"meta": None}

    cache_key = f"meta-proxy:{media_type}:{imdb_id}:v13"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

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
        chinese, chosen_country, credits, locales = await get_chinese_bundle(
            tmdb_type,
            tmdb_id,
        )
    except Exception:
        chinese, chosen_country, credits, locales = None, None, None, []

    # Non-CN/HK: preserve Cinemeta English.
    if not chinese:
        if cine:
            payload = {"meta": cine}
        else:
            try:
                english = await get_details(tmdb_type, tmdb_id, "en-US", False)
                payload = {"meta": tmdb_fallback_meta(media_type, imdb_id, english)}
            except Exception:
                payload = {"meta": None}

        cache.set(cache_key, payload, 12 * 60 * 60)
        return payload

    # CN/HK: start from Cinemeta to keep canonical IMDb and episode IDs.
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
                # Keep both common legacy keys; modern Stremio primarily uses `links`.
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

    if media_type == "series" and tmdb_type == "tv" and locales:
        try:
            await localize_series_episodes(meta, tmdb_id, locales)
        except Exception:
            # Never break the entire detail page because one season localization failed.
            pass

    if chosen_country:
        meta["country"] = chosen_country

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
    .muted{{opacity:.72;font-size:.92rem}}
  </style>
</head>
<body>
  <h1>{APP_NAME}</h1>
  <p>🇨🇳 中国电影 · 🇭🇰 香港电影 · 🇨🇳 中国电视剧 · 🇭🇰 香港电视剧</p>
  <p>Popular · Latest · Top Rated</p>
  <p>CN/HK: 中文标题、简介、演员/导演/编剧（有原生中文名时）以及剧集单集资料。</p>
  <p>其他国家：保留 Cinemeta English metadata。</p>
  <p>{status}</p>
  <p><a class="button" href="{stremio_url}">Install in Stremio</a></p>
  <p>Manifest: <code>{manifest_url}</code></p>
  <p>This addon provides catalogs and metadata only. It does not provide streams.</p>
  <p class="muted">This product uses the TMDB API but is not endorsed or certified by TMDB.</p>
</body>
</html>"""


@app.get("/manifest.json")
async def manifest():
    return JSONResponse(
        MANIFEST,
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.get("/catalog/{media_type}/{catalog_id}.json")
async def catalog(media_type: str, catalog_id: str):
    try:
        payload = await discover_catalog(media_type, catalog_id, 0, "Popular")
        return JSONResponse(
            payload,
            headers={"Cache-Control": "public, max-age=1800"},
        )
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
        skip, sort_mode = parse_extra(extra_args)
        payload = await discover_catalog(
            media_type,
            catalog_id,
            skip,
            sort_mode,
        )
        return JSONResponse(
            payload,
            headers={"Cache-Control": "public, max-age=1800"},
        )
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"TMDB error: {exc.response.status_code}",
        ) from exc


@app.get("/meta/{media_type}/{imdb_id}.json")
async def meta(media_type: str, imdb_id: str):
    try:
        payload = await metadata_proxy(media_type, imdb_id)
        return JSONResponse(
            payload,
            headers={"Cache-Control": "public, max-age=1800"},
        )
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Upstream metadata error: {exc.response.status_code}",
        ) from exc


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "tmdbConfigured": bool(TMDB_API_TOKEN or TMDB_API_KEY),
        "version": APP_VERSION,
        "features": [
            "CN/HK Chinese metadata",
            "native-name credit aliases",
            "localized TV episodes",
            "Popular/Latest/Top Rated",
            "Cinemeta English fallback",
        ],
        "sortOptions": SORT_OPTIONS,
        "catalogs": [cfg["name"] for cfg in CATALOGS.values()],
    }
