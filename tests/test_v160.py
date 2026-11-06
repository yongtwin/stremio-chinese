import base64
import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app

V3_KEY = "0123456789abcdef0123456789abcdef"
V4_TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJhdWQiOiJ4In0.c2lnbmF0dXJl"


def b64(data) -> str:
    raw = json.dumps(data).encode() if not isinstance(data, bytes) else data
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


class TaiwanTests(unittest.IsolatedAsyncioTestCase):
    def test_origin_bucket_priority_is_hk_then_cn_then_tw(self):
        self.assertEqual(app.origin_bucket({"TW"}), "TW")
        self.assertEqual(app.origin_bucket({"CN", "TW"}), "CN")
        self.assertEqual(app.origin_bucket({"HK", "CN", "TW"}), "HK")
        self.assertIsNone(app.origin_bucket({"US", "JP"}))

    def test_taiwan_locale_chain(self):
        self.assertEqual(app.locale_chain("TW"), ["zh-TW", "zh-HK", "zh-CN"])
        self.assertEqual(app.locale_chain("CN"), ["zh-CN", "zh-TW", "zh-HK"])
        self.assertEqual(app.locale_chain("HK"), ["zh-HK", "zh-TW", "zh-CN"])

    async def test_taiwan_titles_get_chinese_bundle_instead_of_english_fallback(self):
        english = {"production_countries": [{"iso_3166_1": "TW"}]}
        preferred = {"title": "台灣片名", "overview": "台灣簡介"}
        with patch.object(
            app, "get_details", new=AsyncMock(side_effect=[english, preferred])
        ):
            merged, country, _, locales = await app.get_chinese_bundle("movie", 1)
        self.assertEqual(country, "TW")
        self.assertEqual(locales[0], "zh-TW")
        self.assertEqual(merged["title"], "台灣片名")

    async def test_search_prefilter_includes_taiwan(self):
        with patch.object(
            app,
            "get_details",
            new=AsyncMock(return_value={"production_countries": [{"iso_3166_1": "TW"}]}),
        ):
            bucket = await app.search_origin_bucket("movie", {"id": 4, "original_language": "zh"})
            by_country = await app.search_origin_bucket(
                "tv", {"id": 5, "original_language": "nan", "origin_country": ["TW"]}
            )
        self.assertEqual(bucket, "TW")
        self.assertEqual(by_country, "TW")


class GenreTests(unittest.IsolatedAsyncioTestCase):
    def test_parse_extra_accepts_sort_options_and_genre_labels(self):
        self.assertEqual(app.parse_extra("genre=%E6%82%AC%E7%96%91.json"), (0, "悬疑"))
        self.assertEqual(app.parse_extra("genre=Latest&skip=20.json"), (20, "Latest"))
        self.assertEqual(app.parse_extra("genre=Nonsense.json"), (0, "Popular"))

    def test_genre_dropdown_lists_sorts_then_genres_only_on_country_rows(self):
        by_id = {c["id"]: c for c in app.MANIFEST["catalogs"]}

        def options(catalog_id):
            return next(e for e in by_id[catalog_id]["extra"] if e["name"] == "genre")["options"]

        self.assertEqual(options("cn_series")[:3], app.SORT_OPTIONS)
        self.assertIn("悬疑", options("cn_series"))
        self.assertIn("恐怖", options("hk_movies"))
        self.assertNotIn("恐怖", options("cn_series"))
        self.assertEqual(options("cn_donghua"), app.SORT_OPTIONS)

    async def run_discover(self, media_type, catalog_id, selection):
        tmdb_get = AsyncMock(return_value={"results": []})
        with (
            patch.object(app, "cache", app.SimpleTTLCache()),
            patch.object(app, "tmdb_get", new=tmdb_get),
        ):
            await app.discover_catalog(media_type, catalog_id, 0, selection)
        return tmdb_get.call_args.args[1]

    async def test_genre_label_becomes_with_genres_sorted_by_popularity(self):
        params = await self.run_discover("series", "cn_series", "悬疑")
        self.assertEqual(params["with_genres"], "9648")
        self.assertEqual(params["sort_by"], "popularity.desc")
        self.assertEqual(params["with_origin_country"], "CN")

    async def test_genre_is_anded_with_the_rows_own_genres(self):
        params = await self.run_discover("series", "cn_donghua", "剧情")
        self.assertEqual(params["with_genres"], "16,18")

    async def test_row_genres_apply_without_a_selection(self):
        donghua = await self.run_discover("series", "cn_donghua", "Popular")
        variety = await self.run_discover("series", "cn_variety", "Latest")
        movies = await self.run_discover("movie", "cn_movies", "Popular")
        self.assertEqual(donghua["with_genres"], "16")
        self.assertEqual(variety["with_genres"], "10764|10767")
        self.assertEqual(variety["sort_by"], "first_air_date.desc")
        self.assertNotIn("with_genres", movies)

    async def test_label_from_the_other_media_type_is_ignored(self):
        # 动作冒险 is a TV genre; the movie list has 动作.
        params = await self.run_discover("movie", "cn_movies", "动作冒险")
        self.assertNotIn("with_genres", params)
        params = await self.run_discover("movie", "cn_movies", "动作")
        self.assertEqual(params["with_genres"], "28")

    async def test_cache_key_includes_the_selection(self):
        tmdb_get = AsyncMock(return_value={"results": []})
        with (
            patch.object(app, "cache", app.SimpleTTLCache()),
            patch.object(app, "tmdb_get", new=tmdb_get),
        ):
            await app.discover_catalog("series", "cn_series", 0, "悬疑")
            await app.discover_catalog("series", "cn_series", 0, "喜剧")
            await app.discover_catalog("series", "cn_series", 0, "悬疑")
        self.assertEqual(tmdb_get.await_count, 2)


class ConfigTests(unittest.TestCase):
    def test_round_trip(self):
        config = app.AddonConfig("hant", ("hk_movies", "tw_series"), V3_KEY)
        decoded = app.decode_config(config.encode())
        self.assertEqual(decoded, config)

        no_key = app.AddonConfig("hans", ("cn_series",))
        self.assertEqual(app.decode_config(no_key.encode()), no_key)
        self.assertNotIn(V3_KEY, repr(config))

    def test_catalogs_keep_manifest_order_and_drop_unknown_ids(self):
        decoded = app.decode_config(b64({"s": "auto", "c": ["tw_series", "bogus", "cn_movies"]}))
        self.assertEqual(decoded.catalogs, ("cn_movies", "tw_series"))

    def test_accepts_v3_key_and_v4_token(self):
        for key in (V3_KEY, V4_TOKEN):
            with self.subTest(key=key[:6]):
                decoded = app.decode_config(b64({"s": "auto", "c": ["cn_movies"], "k": key}))
                self.assertEqual(decoded.tmdb_key, key)

    def test_rejects_bad_configs(self):
        bad = [
            "",
            "!!!",
            "abc",
            b64(b"not json"),
            b64([1, 2]),
            b64({"s": "klingon", "c": ["cn_movies"]}),
            b64({"s": "auto", "c": "cn_movies"}),
            b64({"s": "auto", "c": []}),
            b64({"s": "auto", "c": ["bogus"]}),
        ]
        for segment in bad:
            with self.subTest(segment=segment):
                self.assertIsNone(app.decode_config(segment))

    def test_rejects_bad_key_formats(self):
        for key in (
            "short",
            V3_KEY.upper(),
            V3_KEY + "0",
            "g" * 32,
            "eyJ-no-dots",
            V4_TOKEN + " x",
            12345,
            None,
        ):
            with self.subTest(key=key):
                segment = b64({"s": "auto", "c": ["cn_movies"], "k": key})
                self.assertIsNone(app.decode_config(segment))


def tmdb_response(status=200, payload=None):
    return httpx.Response(
        status,
        json=payload if payload is not None else {"ok": True},
        request=httpx.Request("GET", app.TMDB_BASE + "/x"),
    )


class TmdbKeyTests(unittest.IsolatedAsyncioTestCase):
    async def call(self, config, response=None, **env):
        get = AsyncMock(return_value=response or tmdb_response())
        token = app.current_config.set(config)
        try:
            with (
                patch.object(app.http, "get", new=get),
                patch.object(app, "TMDB_API_TOKEN", env.get("token", "")),
                patch.object(app, "TMDB_API_KEY", env.get("key", "")),
            ):
                try:
                    return get, await app.tmdb_get("/movie/1", {"language": "zh-CN"})
                except Exception as exc:
                    return get, exc
        finally:
            app.current_config.reset(token)

    async def test_v3_key_is_sent_as_api_key_param(self):
        get, _ = await self.call(app.AddonConfig(tmdb_key=V3_KEY), token="server")
        kwargs = get.call_args.kwargs
        self.assertEqual(kwargs["params"]["api_key"], V3_KEY)
        self.assertNotIn("Authorization", kwargs["headers"])

    async def test_v4_token_is_sent_as_bearer_header(self):
        get, _ = await self.call(app.AddonConfig(tmdb_key=V4_TOKEN), key="server")
        kwargs = get.call_args.kwargs
        self.assertEqual(kwargs["headers"]["Authorization"], f"Bearer {V4_TOKEN}")
        self.assertNotIn("api_key", kwargs["params"])

    async def test_without_a_config_key_the_server_key_is_used(self):
        get, _ = await self.call(app.DEFAULT_CONFIG, token="server-token")
        self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], "Bearer server-token")
        get, _ = await self.call(app.DEFAULT_CONFIG, key="server-v3")
        self.assertEqual(get.call_args.kwargs["params"]["api_key"], "server-v3")

    async def test_no_credentials_at_all_is_a_503(self):
        _, result = await self.call(app.DEFAULT_CONFIG)
        self.assertIsInstance(result, HTTPException)
        self.assertEqual(result.status_code, 503)

    async def test_a_user_key_works_even_when_the_server_has_none(self):
        _, result = await self.call(app.AddonConfig(tmdb_key=V3_KEY))
        self.assertEqual(result, {"ok": True})

    async def test_rejected_user_key_never_falls_back_to_the_server_key(self):
        get, result = await self.call(
            app.AddonConfig(tmdb_key=V3_KEY), tmdb_response(401, {}), token="server"
        )
        self.assertIsInstance(result, HTTPException)
        self.assertEqual(result.status_code, 502)
        self.assertIn("rejected", result.detail)
        self.assertNotIn(V3_KEY, result.detail)
        get.assert_awaited_once()

    async def test_upstream_errors_do_not_leak_the_key_in_the_message(self):
        _, result = await self.call(
            app.AddonConfig(tmdb_key=V3_KEY), tmdb_response(500, {}), token="server"
        )
        self.assertIsInstance(result, httpx.HTTPStatusError)
        self.assertEqual(result.response.status_code, 500)
        self.assertNotIn(V3_KEY, str(result))


class ConfiguredRouteTests(unittest.TestCase):
    def test_configured_manifest_lists_only_selected_catalogs(self):
        segment = app.AddonConfig("hans", ("tw_series", "cn_movies"), V3_KEY).encode()
        with TestClient(app.app) as client:
            response = client.get(f"/{segment}/manifest.json")
            root = client.get("/manifest.json")
            legacy = client.get("/v140/manifest.json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [c["id"] for c in response.json()["catalogs"]], ["cn_movies", "tw_series"]
        )
        self.assertNotIn(V3_KEY, response.text)
        self.assertEqual(response.json()["id"], root.json()["id"])
        self.assertEqual(len(root.json()["catalogs"]), 8)
        self.assertEqual(legacy.status_code, 200)
        self.assertEqual(legacy.json(), root.json())

    def test_invalid_config_is_404_on_every_configured_route(self):
        with TestClient(app.app) as client:
            for path in (
                "/AAAAAAAA/manifest.json",
                "/AAAAAAAA/catalog/movie/cn_movies.json",
                "/AAAAAAAA/catalog/movie/cn_movies/genre=Latest.json",
                "/AAAAAAAA/meta/movie/tch%3Amovie%3A1.json",
                "/AAAAAAAA/configure",
            ):
                with self.subTest(path=path):
                    self.assertEqual(client.get(path).status_code, 404)

    def test_configured_catalog_routes_apply_config_and_skip_disabled_rows(self):
        seen = {}

        async def discover(media_type, catalog_id, skip, selection):
            seen["script"] = app.current_config.get().script
            seen["key"] = app.current_config.get().tmdb_key
            return {"metas": [{"id": catalog_id}]}

        segment = app.AddonConfig("hant", ("cn_movies",), V3_KEY).encode()
        with (
            patch.object(app, "discover_catalog", new=discover),
            TestClient(app.app) as client,
        ):
            both = [
                client.get(f"/{segment}/catalog/movie/cn_movies.json"),
                client.get(f"/{segment}/catalog/movie/cn_movies/genre=Latest.json"),
            ]
            disabled = client.get(f"/{segment}/catalog/movie/hk_movies.json")

        for response in both:
            self.assertEqual(response.json(), {"metas": [{"id": "cn_movies"}]})
        self.assertEqual(seen, {"script": "hant", "key": V3_KEY})
        self.assertEqual(disabled.json(), {"metas": []})
        # The config must not leak into later requests.
        self.assertEqual(app.current_config.get(), app.DEFAULT_CONFIG)

    def test_configured_meta_honours_traditional_script(self):
        locales_requested = []

        async def details(tmdb_type, tmdb_id, locale, include_credits=False):
            locales_requested.append(locale)
            if locale == "en-US":
                return {"production_countries": [{"iso_3166_1": "CN"}]}
            return {"title": "繁體片名", "overview": "繁體簡介"}

        item = "tch%3Amovie%3A1%3Att1234567"
        segment = app.AddonConfig("hant", tuple(app.CATALOG_IDS)).encode()
        with (
            patch.object(app, "cache", app.SimpleTTLCache()),
            patch.object(app, "get_details", side_effect=details),
            patch.object(app, "cinemeta_meta", new=AsyncMock(return_value=None)),
            TestClient(app.app) as client,
        ):
            response = client.get(f"/{segment}/meta/movie/{item}.json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["meta"]["name"], "繁體片名")
        # A CN title in hant mode asks for traditional Chinese first.
        self.assertEqual(locales_requested[:2], ["en-US", "zh-TW"])

    def test_meta_cache_is_separate_per_script(self):
        key = {}

        async def fake_cache_get(cache_key):
            key[app.current_config.get().script] = cache_key
            return {"meta": {"id": "cached"}}

        with patch.object(app, "cache_get", new=fake_cache_get):
            for script in ("hans", "hant"):
                segment = app.AddonConfig(script).encode()
                with TestClient(app.app) as client:
                    client.get(f"/{segment}/meta/movie/tch%3Amovie%3A1.json")
        self.assertNotEqual(key["hans"], key["hant"])

    def test_catalog_language_follows_script_setting(self):
        hk = app.CATALOGS[("movie", "hk_movies")]
        cn = app.CATALOGS[("movie", "cn_movies")]
        tw = app.CATALOGS[("series", "tw_series")]
        auto, hans, hant = (app.AddonConfig(s) for s in ("auto", "hans", "hant"))
        self.assertEqual(
            [app.catalog_language(c, auto) for c in (cn, hk, tw)], ["zh-CN", "zh-HK", "zh-TW"]
        )
        self.assertEqual(
            [app.catalog_language(c, hans) for c in (cn, hk, tw)], ["zh-CN"] * 3
        )
        self.assertEqual(
            [app.catalog_language(c, hant) for c in (cn, hk, tw)], ["zh-TW", "zh-HK", "zh-TW"]
        )

    def test_configure_pages_render_and_prefill(self):
        segment = app.AddonConfig("hant", ("cn_movies",), V3_KEY).encode()
        with TestClient(app.app) as client:
            fresh = client.get("/configure")
            existing = client.get(f"/{segment}/configure")

        self.assertEqual(fresh.status_code, 200)
        self.assertIn("themoviedb.org/settings/api", fresh.text)
        self.assertIn("api.themoviedb.org/3/configuration", fresh.text)
        self.assertNotIn(V3_KEY, fresh.text)
        self.assertIn(V3_KEY, existing.text)
        self.assertIn('"script": "hant"', existing.text)
        for catalog_id in app.CATALOG_IDS:
            self.assertIn(catalog_id, fresh.text)


class PosterAndTrailerTests(unittest.IsolatedAsyncioTestCase):
    def test_pick_chinese_poster_prefers_region_then_png_then_votes(self):
        images = {
            "posters": [
                {"iso_639_1": "en", "file_path": "/en.jpg", "vote_average": 9},
                {"iso_639_1": "zh", "iso_3166_1": "TW", "file_path": "/tw.jpg", "vote_average": 8},
                {"iso_639_1": "zh", "iso_3166_1": "CN", "file_path": "/cn-low.jpg", "vote_average": 1},
                {"iso_639_1": "zh", "iso_3166_1": "CN", "file_path": "/cn-high.jpg", "vote_average": 5},
            ]
        }
        self.assertEqual(
            app.pick_chinese_image(images, "posters", ["zh-CN", "zh-TW", "zh-HK"]),
            "https://image.tmdb.org/t/p/w500/cn-high.jpg",
        )
        self.assertEqual(
            app.pick_chinese_image(images, "posters", ["zh-TW", "zh-HK", "zh-CN"]),
            "https://image.tmdb.org/t/p/w500/tw.jpg",
        )
        self.assertIsNone(app.pick_chinese_image({"posters": [images["posters"][0]]}, "posters", ["zh-CN"]))
        self.assertIsNone(app.pick_chinese_image({}, "posters", ["zh-CN"]))

    def test_pick_chinese_trailers(self):
        videos = {
            "results": [
                {"iso_639_1": "en", "site": "YouTube", "type": "Trailer", "key": "ENGLISH1"},
                {"iso_639_1": "zh", "site": "Vimeo", "type": "Trailer", "key": "VIMEO123"},
                {"iso_639_1": "zh", "site": "YouTube", "type": "Featurette", "key": "FEATURE1"},
                {"iso_639_1": "zh", "iso_3166_1": "CN", "site": "YouTube", "type": "Teaser",
                 "key": "TEASER01", "name": "先导预告"},
                {"iso_639_1": "zh", "iso_3166_1": "TW", "site": "YouTube", "type": "Trailer",
                 "key": "TWTRAIL1", "name": "台版预告"},
                {"iso_639_1": "zh", "iso_3166_1": "CN", "site": "YouTube", "type": "Trailer",
                 "key": "CNOLD001", "published_at": "2020-01-01T00:00:00.000Z"},
                {"iso_639_1": "zh", "iso_3166_1": "CN", "site": "YouTube", "type": "Trailer",
                 "key": "CNNEW001", "name": "正式预告", "published_at": "2024-01-01T00:00:00.000Z"},
                {"iso_639_1": "zh", "iso_3166_1": "CN", "site": "YouTube", "type": "Trailer",
                 "key": "bad key!"},
            ]
        }
        trailers = app.pick_chinese_trailers(videos, ["zh-CN", "zh-TW", "zh-HK"])
        self.assertEqual(
            [t["ytId"] for t in trailers], ["CNNEW001", "CNOLD001", "TWTRAIL1"]
        )
        self.assertEqual(trailers[0], {"title": "正式预告", "ytId": "CNNEW001"})
        self.assertEqual(app.pick_chinese_trailers({"results": []}, ["zh-CN"]), [])
        self.assertEqual(app.pick_chinese_trailers(None, ["zh-CN"]), [])

    async def meta_with(self, details, cinemeta):
        with (
            patch.object(app, "cache", app.SimpleTTLCache()),
            patch.object(app, "cinemeta_meta", new=AsyncMock(return_value=cinemeta)),
            patch.object(
                app,
                "get_chinese_bundle",
                new=AsyncMock(return_value=(details, "CN", {}, ["zh-CN", "zh-TW", "zh-HK"])),
            ),
        ):
            return (await app.metadata_proxy("movie", "tch:movie:1:tt1234567"))["meta"]

    async def test_chinese_poster_and_trailers_replace_cinemeta_ones(self):
        cinemeta = {
            "id": "tt1234567",
            "type": "movie",
            "name": "English",
            "poster": "https://images.metahub.space/poster/medium/tt1234567/img",
            "trailerStreams": [{"title": "English", "ytId": "ENGLISH1"}],
            "trailers": [{"source": "ENGLISH1", "type": "Trailer"}],
        }
        details = {
            "title": "中文片名",
            "poster_path": "/default.jpg",
            "images": {
                "posters": [{"iso_639_1": "zh", "iso_3166_1": "CN", "file_path": "/zh.jpg"}]
            },
            "videos": {
                "results": [
                    {"iso_639_1": "zh", "iso_3166_1": "CN", "site": "YouTube",
                     "type": "Trailer", "key": "ZHTRAIL1", "name": "中文预告"}
                ]
            },
        }
        meta = await self.meta_with(details, cinemeta)

        self.assertEqual(meta["poster"], "https://image.tmdb.org/t/p/w500/zh.jpg")
        self.assertEqual(meta["trailerStreams"], [{"title": "中文预告", "ytId": "ZHTRAIL1"}])
        self.assertEqual(meta["trailers"], [{"source": "ZHTRAIL1", "type": "Trailer"}])

    async def test_falls_back_to_tmdb_poster_and_keeps_cinemeta_trailers(self):
        cinemeta = {
            "id": "tt1234567",
            "type": "movie",
            "name": "English",
            "trailerStreams": [{"title": "English", "ytId": "ENGLISH1"}],
            "trailers": [{"source": "ENGLISH1", "type": "Trailer"}],
        }
        details = {
            "title": "中文片名",
            "poster_path": "/default.jpg",
            "images": {"posters": []},
            "videos": {"results": []},
        }
        meta = await self.meta_with(details, cinemeta)

        self.assertEqual(meta["poster"], "https://image.tmdb.org/t/p/w500/default.jpg")
        self.assertEqual(meta["trailerStreams"], cinemeta["trailerStreams"])
        self.assertEqual(meta["trailers"], cinemeta["trailers"])


class SeasonPrefetchTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def bulk_response(path_params):
        numbers = [
            int(part.split("/")[1])
            for part in path_params["append_to_response"].split(",")
        ]
        return {
            "id": 7,
            **{
                f"season/{n}": {"season_number": n, "episodes": [{"episode_number": 1}]}
                for n in numbers
            },
        }

    async def test_bulk_prefetch_makes_one_call_per_twenty_seasons(self):
        async def fake_tmdb(path, params=None):
            return self.bulk_response(params)

        tmdb_get = AsyncMock(side_effect=fake_tmdb)
        with (
            patch.object(app, "cache", app.SimpleTTLCache()),
            patch.object(app, "tmdb_get", new=tmdb_get),
        ):
            await app.prefetch_seasons(7, list(range(1, 46)), "zh-CN")
            calls = sorted(
                len(c.args[1]["append_to_response"].split(",")) for c in tmdb_get.await_args_list
            )
            cached = [
                await app.get_season_details(7, n, "zh-CN") for n in (1, 20, 21, 45)
            ]

        self.assertEqual(calls, [5, 20, 20])
        self.assertEqual(tmdb_get.await_count, 3)  # reading back hit the cache
        self.assertTrue(all(c["episodes"] for c in cached))
        self.assertEqual(tmdb_get.await_args_list[0].args[0], "/tv/7")
        self.assertEqual(tmdb_get.await_args_list[0].args[1]["language"], "zh-CN")

    async def test_prefetch_skips_cached_seasons_and_single_misses(self):
        tmdb_get = AsyncMock(side_effect=lambda path, params=None: self.bulk_response(params))
        with (
            patch.object(app, "cache", app.SimpleTTLCache()),
            patch.object(app, "tmdb_get", new=tmdb_get),
        ):
            await app.prefetch_seasons(7, [1, 2, 3], "zh-CN")
            self.assertEqual(tmdb_get.await_count, 1)
            await app.prefetch_seasons(7, [1, 2, 3], "zh-CN")  # all cached
            await app.prefetch_seasons(7, [1, 2, 3, 4], "zh-CN")  # only 4 missing
            await app.prefetch_seasons(7, [4], "zh-CN")
            self.assertEqual(tmdb_get.await_count, 1)

    async def test_prefetch_failure_is_swallowed_and_leaves_seasons_to_fall_back(self):
        with (
            patch.object(app, "cache", app.SimpleTTLCache()),
            patch.object(app, "tmdb_get", new=AsyncMock(side_effect=RuntimeError("boom"))),
        ):
            await app.prefetch_seasons(7, [1, 2, 3], "zh-CN")
            self.assertIsNone(await app.cache_get(app.season_cache_key(7, 1, "zh-CN")))

    async def test_long_series_uses_bulk_prefetch_for_all_seasons(self):
        numbers = list(range(1, 41))
        details = {"seasons": [{"season_number": n} for n in numbers]}

        async def fake_tmdb(path, params=None):
            if path == "/tv/9":
                return self.bulk_response(params)
            raise AssertionError(f"unexpected per-season request: {path}")

        tmdb_get = AsyncMock(side_effect=fake_tmdb)
        meta: dict = {}
        with (
            patch.object(app, "cache", app.SimpleTTLCache()),
            patch.object(app, "tmdb_get", new=tmdb_get),
        ):
            await app.localize_series_episodes(meta, 9, "tmdb:9", details, ["zh-CN"])

        # Episodes without CJK text still count; 40 seasons cost two calls, not forty.
        self.assertEqual(tmdb_get.await_count, 2)
        self.assertEqual({v["season"] for v in meta["videos"]}, set(numbers))


class SharedCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_cache_set_writes_through_to_redis_with_ttl(self):
        redis = AsyncMock(return_value="OK")
        with (
            patch.object(app, "cache", app.SimpleTTLCache()),
            patch.object(app, "REDIS_URL", "https://redis.example"),
            patch.object(app, "redis_command", new=redis),
        ):
            await app.cache_set("k", {"v": "中文"}, 60)

        command = redis.await_args.args
        self.assertEqual(command[:2], ("SET", app.REDIS_PREFIX + "k"))
        self.assertEqual(command[3:], ("EX", 60))
        stored = json.loads(command[2])
        self.assertEqual(stored["v"], {"v": "中文"})
        self.assertGreater(stored["e"], 0)

    async def test_cache_get_prefers_memory_then_redis_and_repopulates_memory(self):
        memory = app.SimpleTTLCache()
        redis = AsyncMock(return_value=None)
        with (
            patch.object(app, "cache", memory),
            patch.object(app, "REDIS_URL", "https://redis.example"),
            patch.object(app, "redis_command", new=redis),
        ):
            memory.set("hit", [1], 60)
            self.assertEqual(await app.cache_get("hit"), [1])
            redis.assert_not_awaited()

            # Another instance wrote it: found in Redis, then kept in memory.
            import time

            redis.return_value = json.dumps({"e": time.time() + 120, "v": {"a": 1}})
            self.assertEqual(await app.cache_get("shared"), {"a": 1})
            redis.return_value = None
            self.assertEqual(await app.cache_get("shared"), {"a": 1})
            self.assertEqual(redis.await_count, 1)

    async def test_cache_get_ignores_expired_corrupt_and_missing_redis_values(self):
        import time

        redis = AsyncMock()
        with (
            patch.object(app, "cache", app.SimpleTTLCache()),
            patch.object(app, "redis_command", new=redis),
        ):
            for stored in (
                json.dumps({"e": time.time() - 5, "v": 1}),
                "not json",
                json.dumps({"v": 1}),
                json.dumps({"e": time.time() + 60, "v": None}),
                None,
            ):
                redis.return_value = stored
                self.assertIsNone(await app.cache_get("x"))

    async def test_falsy_values_are_valid_cache_hits(self):
        # imdb lookups cache "" for titles without an IMDb ID.
        with patch.object(app, "cache", app.SimpleTTLCache()):
            await app.cache_set("empty", "", 60)
            self.assertEqual(await app.cache_get("empty"), "")

    async def test_oversize_values_stay_in_memory_only(self):
        redis = AsyncMock()
        big = {"blob": "x" * (app.REDIS_MAX_BYTES + 1)}
        with (
            patch.object(app, "cache", app.SimpleTTLCache()),
            patch.object(app, "REDIS_URL", "https://redis.example"),
            patch.object(app, "redis_command", new=redis),
        ):
            await app.cache_set("big", big, 60)
            self.assertEqual(await app.cache_get("big"), big)
        redis.assert_not_awaited()

    async def test_without_redis_nothing_is_sent_and_nothing_fails(self):
        redis = AsyncMock()
        with (
            patch.object(app, "cache", app.SimpleTTLCache()),
            patch.object(app, "REDIS_URL", ""),
            patch.object(app, "REDIS_TOKEN", ""),
        ):
            await app.cache_set("k", 1, 60)
            self.assertEqual(await app.cache_get("k"), 1)
            self.assertIsNone(await app.redis_command("GET", "k"))
        redis.assert_not_awaited()

    async def test_redis_errors_never_fail_a_request(self):
        with (
            patch.object(app, "REDIS_URL", "https://redis.example"),
            patch.object(app, "REDIS_TOKEN", "t"),
            patch.object(app.http, "post", new=AsyncMock(side_effect=httpx.ConnectError("x"))),
        ):
            self.assertIsNone(await app.redis_command("GET", "k"))


class SupportLinkTests(unittest.TestCase):
    URL = "https://ko-fi.com/yongtwinchin"

    def test_home_page_links_to_kofi_in_a_new_tab(self):
        with TestClient(app.app) as client:
            home = client.get("/").text
        self.assertIn(f'href="{self.URL}"', home)
        self.assertIn('target="_blank" rel="noopener"', home)
        self.assertIn("kofi", home)
        # Install stays the primary action and comes first.
        self.assertLess(home.index('href="/configure"'), home.index(self.URL))

    def test_configure_pages_link_to_kofi(self):
        segment = app.AddonConfig("hans", ("cn_movies",)).encode()
        with TestClient(app.app) as client:
            pages = [client.get("/configure").text, client.get(f"/{segment}/configure").text]
        for page in pages:
            self.assertGreaterEqual(page.count(f'href="{self.URL}"'), 2)  # footer + install area
            self.assertNotIn("__SUPPORT__", page)
            self.assertNotIn("__NAME__", page)

    def test_manifest_description_mentions_kofi_for_every_manifest(self):
        segment = app.AddonConfig("hant", ("tw_series",), V3_KEY).encode()
        with TestClient(app.app) as client:
            manifests = [
                client.get("/manifest.json").json(),
                client.get("/v140/manifest.json").json(),
                client.get(f"/{segment}/manifest.json").json(),
            ]
        for manifest in manifests:
            self.assertIn("ko-fi.com/yongtwinchin", manifest["description"])
            self.assertEqual(manifest["id"], "com.twin.chinese-hk-catalog.v141")
        self.assertIn("ko-fi.com/yongtwinchin", app.MANIFEST["description"])

    def test_health_reports_support_url(self):
        with TestClient(app.app) as client:
            self.assertEqual(client.get("/health").json()["supportUrl"], self.URL)


if __name__ == "__main__":
    unittest.main()
