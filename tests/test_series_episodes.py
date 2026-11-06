import unittest
from unittest.mock import AsyncMock, patch

import app
from fastapi.testclient import TestClient


class SeriesEpisodeMetadataTests(unittest.IsolatedAsyncioTestCase):
    def test_manifest_and_install_page_expose_one_stable_eight_catalog_addon(self):
        with TestClient(app.app) as client:
            manifest_response = client.get("/manifest.json")
            install_response = client.get("/")
            health_response = client.get("/health")

        manifest = manifest_response.json()
        catalog_ids = {catalog["id"] for catalog in manifest["catalogs"]}
        # Same ID as the deployed addon so existing installs update in place.
        self.assertEqual(manifest["id"], "com.twin.chinese-hk-catalog.v141")
        self.assertEqual(manifest["version"], "1.6.0")
        self.assertEqual(len(manifest["catalogs"]), 8)
        self.assertEqual(
            catalog_ids,
            {
                "cn_movies", "hk_movies", "tw_movies",
                "cn_series", "hk_series", "tw_series",
                "cn_donghua", "cn_variety",
            },
        )
        self.assertTrue(manifest["behaviorHints"]["configurable"])
        # New installs go through /configure; the root manifest is not advertised.
        self.assertIn('href="/configure"', install_response.text)
        self.assertNotIn('href="stremio://testserver/manifest.json"', install_response.text)
        self.assertEqual(health_response.json()["version"], "1.6.0")

    async def test_hong_kong_metadata_uses_traditional_chinese_fallback_when_present(self):
        english = {
            "production_countries": [{"iso_3166_1": "HK"}],
        }
        preferred = {"title": "English title", "overview": "English overview"}
        fallback = {"title": "繁體片名", "overview": "繁體簡介"}

        with patch.object(
            app,
            "get_details",
            new=AsyncMock(side_effect=[english, preferred, fallback]),
        ) as fetch_details:
            merged, country, _, locales = await app.get_chinese_bundle("movie", 12345)

        self.assertEqual(country, "HK")
        self.assertEqual(merged["title"], "繁體片名")
        self.assertEqual(merged["overview"], "繁體簡介")
        self.assertEqual(locales, ["zh-HK", "zh-TW", "zh-CN"])
        self.assertEqual(fetch_details.await_count, 3)

    async def test_movie_meta_keeps_imdb_default_video_id_for_stream_addons(self):
        item_id = "tch:movie:603692:tt1234567"
        cinemeta = {
            "id": "tt1234567",
            "type": "movie",
            "name": "English title",
            "behaviorHints": {"otherHint": "preserved"},
        }

        with (
            patch.object(app, "cache", app.SimpleTTLCache()),
            patch.object(app, "cinemeta_meta", new=AsyncMock(return_value=cinemeta)),
            patch.object(
                app,
                "get_chinese_bundle",
                new=AsyncMock(
                    return_value=(
                        {"title": "中文片名"},
                        "CN",
                        {},
                        ["zh-CN", "zh-TW", "zh-HK"],
                    )
                ),
            ),
        ):
            response = await app.metadata_proxy("movie", item_id)

        meta = response["meta"]
        self.assertEqual(meta["id"], item_id)
        self.assertEqual(meta["behaviorHints"]["defaultVideoId"], "tt1234567")
        self.assertEqual(meta["behaviorHints"]["otherHint"], "preserved")

    def test_episode_id_rejects_boolean_or_non_positive_numbers(self):
        for season, episode in ((True, 1), (1, False), (0, 1), (1, 0)):
            with self.subTest(season=season, episode=episode):
                with self.assertRaises(ValueError):
                    app.make_episode_id("tt7654321", season, episode)

    def test_season_list_ignores_specials_and_caps_at_sixty(self):
        details = {"seasons": [{"season_number": number} for number in range(66)]}

        self.assertEqual(app.MAX_SEASONS, 60)
        self.assertEqual(app.season_numbers_from_details(details), list(range(1, 61)))
        self.assertEqual(
            app.season_numbers_from_details(
                {"seasons": [{"season_number": 0}], "number_of_seasons": 3}
            ),
            [1, 2, 3],
        )

    def test_catalog_sort_extras_map_to_tmdb_ordering(self):
        self.assertEqual(app.parse_extra(None), (0, "Popular"))
        self.assertEqual(app.parse_extra("genre=Top%20Rated&skip=40.json"), (40, "Top Rated"))

        popular = app.discover_sort_params("movie", "Popular")
        latest_movie = app.discover_sort_params("movie", "Latest")
        latest_series = app.discover_sort_params("tv", "Latest")
        top_rated = app.discover_sort_params("series", "Top Rated")

        self.assertEqual(popular, {"sort_by": "popularity.desc"})
        self.assertEqual(latest_movie["sort_by"], "primary_release_date.desc")
        self.assertIn("primary_release_date.lte", latest_movie)
        self.assertEqual(latest_series["sort_by"], "first_air_date.desc")
        self.assertIn("first_air_date.lte", latest_series)
        self.assertEqual(top_rated, {"sort_by": "vote_average.desc", "vote_count.gte": "50"})

    async def test_localized_season_fills_missing_chinese_fields_from_fallback_locale(self):
        preferred = {
            "episodes": [
                {"episode_number": 1, "name": "中文标题", "overview": ""}
            ]
        }
        fallback = {
            "episodes": [
                {"episode_number": 1, "name": "繁體標題", "overview": "中文简介"}
            ]
        }

        with patch.object(
            app,
            "get_season_details",
            new=AsyncMock(side_effect=[preferred, fallback]),
        ) as fetch_season:
            result = await app.localized_season(12345, 1, ["zh-CN", "zh-TW"])

        self.assertEqual(
            result["episodes"][0]["name"],
            "中文标题",
        )
        self.assertEqual(result["episodes"][0]["overview"], "中文简介")
        self.assertEqual(fetch_season.await_count, 2)

    def test_existing_series_videos_are_rewritten_to_canonical_episode_ids(self):
        videos = app.build_series_videos(
            "tt7654321",
            {1: {"episodes": []}},
            [
                {
                    "id": "tch:series:12345:tt7654321",
                    "season": 1,
                    "episode": 2,
                    "title": None,
                }
            ],
        )

        self.assertEqual(videos[0]["id"], "tt7654321:1:2")
        self.assertEqual(videos[0]["title"], "Episode 2")

    async def test_failed_tmdb_season_fetch_preserves_cinemeta_episodes_with_canonical_ids(self):
        async def fetch_season(_tv_id, season_number, _locale):
            if season_number == 2:
                raise RuntimeError("temporary TMDB failure")
            return {
                "episodes": [
                    {"episode_number": 1, "name": "第一集", "overview": "简介"}
                ]
            }

        meta = {
            "videos": [
                {
                    "id": "tt7654321:2:1",
                    "title": "Second season episode",
                    "season": 2,
                    "episode": 1,
                }
            ]
        }

        with (
            patch.object(app, "get_season_details", side_effect=fetch_season),
            patch.object(app, "prefetch_seasons", new=AsyncMock()),
        ):
            await app.localize_series_episodes(
                meta,
                12345,
                "tt7654321",
                {"seasons": [{"season_number": 1}, {"season_number": 2}]},
                ["zh-CN"],
            )

        self.assertEqual(
            [video["id"] for video in meta["videos"]],
            ["tt7654321:1:1", "tt7654321:2:1"],
        )

    async def test_generates_canonical_tmdb_episodes_when_cinemeta_has_none(self):
        item_id = "tch:series:12345:tt7654321"
        details = {
            "name": "中文剧名",
            "seasons": [
                {"season_number": 0},
                {"season_number": 1},
            ],
        }
        cinemeta = {
            "id": "tt7654321",
            "type": "series",
            "name": "English Series",
            "videos": [],
        }
        season = {
            "episodes": [
                {
                    "episode_number": 1,
                    "name": "第一集",
                    "overview": "第一集简介",
                    "air_date": "2024-01-02",
                    "still_path": "/episode.jpg",
                    "runtime": 45,
                }
            ]
        }

        with (
            patch.object(app, "cache", app.SimpleTTLCache()),
            patch.object(app, "cinemeta_meta", new=AsyncMock(return_value=cinemeta)),
            patch.object(
                app,
                "get_chinese_bundle",
                new=AsyncMock(return_value=(details, "CN", {}, ["zh-CN", "zh-TW", "zh-HK"])),
            ),
            patch.object(app, "get_season_details", new=AsyncMock(return_value=season)) as fetch_season,
        ):
            response = await app.metadata_proxy("series", item_id)

        self.assertEqual(response["meta"]["id"], item_id)
        self.assertEqual(
            response["meta"]["videos"],
            [
                {
                    "id": "tt7654321:1:1",
                    "title": "第一集",
                    "season": 1,
                    "episode": 1,
                    "overview": "第一集简介",
                    "released": "2024-01-02T00:00:00.000Z",
                    "releaseInfo": "2024",
                    "thumbnail": "https://image.tmdb.org/t/p/w500/episode.jpg",
                    "runtime": "45 min",
                }
            ],
        )
        fetch_season.assert_awaited_once_with(12345, 1, "zh-CN")


# Mirrors stremio-core `Video` (src/types/resource/meta_item.rs):
#   #[serde(default, alias = "name")]   pub title: String
#   #[serde(alias = "stream", default)] pub streams: Vec<Stream>
#   #[serde(flatten)] series_info: Option<SeriesInfo { season: u32, episode: u32 }>
# serde rejects a field given under both its name and its alias ("duplicate field"),
# and one bad video fails the whole meta, so Stremio shows "No metadata was found!".
STREMIO_VIDEO_ALIASES = {"title": "name", "streams": "stream"}


def stremio_core_video_errors(video: dict) -> list[str]:
    errors = []
    for field, alias in STREMIO_VIDEO_ALIASES.items():
        if field in video and alias in video:
            errors.append(f"duplicate field `{field}` (also sent as `{alias}`)")
    if not isinstance(video.get("id"), str):
        errors.append("id must be a string")
    title = video.get("title", video.get("name", ""))
    if not isinstance(title, str):
        errors.append("title must be a string")
    for field in ("season", "episode"):
        if field in video and (type(video[field]) is not int or video[field] < 0):
            errors.append(f"{field} must be u32")
    return errors


class StremioClientCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_cinemeta_episode_names_do_not_collide_with_localized_titles(self):
        """Regression for 仙逆 (tch:series:223911:tt27432264): Cinemeta videos carry
        `name`, we add a Chinese `title`, and Stremio Web rejected the meta."""
        item_id = "tch:series:223911:tt27432264"
        cinemeta = {
            "id": "tt27432264",
            "type": "series",
            "name": "Renegade Immortal",
            "videos": [
                {
                    "id": "tt27432264:1:1",
                    "name": "Leaving Home",
                    "season": 1,
                    "number": 1,
                    "episode": 1,
                    "firstAired": "2023-09-25T18:00:00.000Z",
                    "tvdb_id": 9945064,
                    "rating": "0",
                    "released": "2023-09-25T18:00:00.000Z",
                },
                {
                    "id": "tt27432264:1:2",
                    "name": "English Only Title",
                    "season": 1,
                    "number": 2,
                    "episode": 2,
                },
            ],
        }
        details = {"name": "仙逆", "seasons": [{"season_number": 1}]}
        season = {
            "episodes": [
                {"episode_number": 1, "name": "离乡", "overview": "少年王林"},
                {"episode_number": 2, "name": "Episode 2", "overview": ""},
            ]
        }

        with (
            patch.object(app, "cache", app.SimpleTTLCache()),
            patch.object(app, "cinemeta_meta", new=AsyncMock(return_value=cinemeta)),
            patch.object(
                app,
                "get_chinese_bundle",
                new=AsyncMock(return_value=(details, "CN", {}, ["zh-CN"])),
            ),
            patch.object(app, "get_season_details", new=AsyncMock(return_value=season)),
        ):
            response = await app.metadata_proxy("series", item_id)

        meta = response["meta"]
        self.assertEqual(meta["id"], item_id)
        self.assertEqual(meta["name"], "仙逆")
        for video in meta["videos"]:
            with self.subTest(video=video["id"]):
                self.assertEqual(stremio_core_video_errors(video), [])

        first, second = meta["videos"]
        self.assertEqual(first["id"], "tt27432264:1:1")
        self.assertEqual(first["title"], "离乡")
        # No Chinese title on TMDB: keep Cinemeta's text, now under `title`.
        self.assertEqual(second["id"], "tt27432264:1:2")
        self.assertEqual(second["title"], "English Only Title")

    async def test_non_chinese_fallback_meta_is_also_sanitized(self):
        cinemeta = {
            "id": "tt1111111",
            "type": "series",
            "name": "Show",
            "videos": [
                {"id": "tt1111111:1:1", "title": "A", "name": "A", "season": 1, "episode": 1}
            ],
        }
        with (
            patch.object(app, "cache", app.SimpleTTLCache()),
            patch.object(app, "cinemeta_meta", new=AsyncMock(return_value=cinemeta)),
            patch.object(
                app, "get_chinese_bundle", new=AsyncMock(return_value=(None, None, None, []))
            ),
            patch.object(app, "get_details", new=AsyncMock(return_value={})),
        ):
            response = await app.metadata_proxy("series", "tch:series:1:tt1111111")

        self.assertEqual(
            [stremio_core_video_errors(v) for v in response["meta"]["videos"]], [[]]
        )

    def test_localized_links_replace_cinemeta_english_categories(self):
        cinemeta_links = [
            {"name": "8.5", "category": "imdb", "url": "https://imdb.com/title/tt1"},
            {"name": "Show", "category": "share", "url": "https://www.strem.io/s/x"},
            {"name": "Animation", "category": "Genres", "url": "stremio:///discover/x"},
            {"name": "Zekun Shi", "category": "Cast", "url": "stremio:///search?search=Z"},
            {"name": "Er Gen", "category": "Writers", "url": "stremio:///search?search=E"},
            {"name": "Someone", "category": "Directors", "url": "stremio:///search?search=S"},
        ]

        links = app.overlay_localized_links(
            cinemeta_links, ["史泽鲲"], ["石头熊"], [], ["动画"]
        )
        by_category: dict[str, list[str]] = {}
        for link in links:
            by_category.setdefault(link["category"], []).append(link["name"])

        self.assertEqual(
            by_category,
            {
                "imdb": ["8.5"],
                "share": ["Show"],
                # No localized writers: keep Cinemeta's rather than show none.
                "Writers": ["Er Gen"],
                "actor": ["史泽鲲"],
                "director": ["石头熊"],
                "genre": ["动画"],
            },
        )

    def test_normalize_video_aliases(self):
        self.assertEqual(
            app.normalize_video_aliases({"id": "x", "name": "N", "stream": {"url": "u"}}),
            {"id": "x", "title": "N", "streams": {"url": "u"}},
        )
        self.assertEqual(
            app.normalize_video_aliases({"id": "x", "title": "T", "name": "N"}),
            {"id": "x", "title": "T"},
        )

    def test_cors_header_present_even_without_origin_header(self):
        """Responses are CDN-cached (Cache-Control: public). A first request without
        an Origin header must still cache a copy that browsers accept."""
        payload = {"meta": None}
        with (
            patch.object(app, "metadata_proxy", new=AsyncMock(return_value=payload)),
            TestClient(app.app) as client,
        ):
            responses = [
                client.get("/manifest.json"),
                client.get("/meta/series/tch%3Aseries%3A223911%3Att27432264.json"),
                client.get(
                    "/meta/series/tch%3Aseries%3A223911%3Att27432264.json",
                    headers={"Origin": "https://web.stremio.com"},
                ),
            ]
            preflight = client.options(
                "/meta/series/x.json",
                headers={
                    "Origin": "https://web.stremio.com",
                    "Access-Control-Request-Method": "GET",
                },
            )

        for response in responses:
            with self.subTest(url=str(response.url)):
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["access-control-allow-origin"], "*")
        self.assertIn(preflight.status_code, (200, 204))
        self.assertEqual(preflight.headers["access-control-allow-origin"], "*")



class TmdbOnlyTitleTests(unittest.IsolatedAsyncioTestCase):
    def test_custom_id_round_trips_with_and_without_imdb(self):
        self.assertEqual(app.make_custom_id("series", 5, "tt1"), "tch:series:5:tt1")
        self.assertEqual(app.make_custom_id("series", 5, None), "tch:series:5")
        self.assertEqual(app.parse_custom_id("series", "tch:series:5:tt1"), (5, "tt1"))
        self.assertEqual(app.parse_custom_id("series", "tch:series:5"), (5, None))
        for bad in ("tch:series:x", "tch:movie:5", "tch:series:5:xx1", "tch:series:0"):
            with self.subTest(bad=bad):
                self.assertIsNone(app.parse_custom_id("series", bad))
        self.assertEqual(app.make_episode_id("tmdb:5", 1, 2), "tmdb:5:1:2")

    async def test_catalog_keeps_titles_without_imdb_but_skips_failed_lookups(self):
        results = [
            {"id": 1, "name": "有IMDb", "poster_path": "/a.jpg"},
            {"id": 2, "name": "无IMDb", "poster_path": "/b.jpg"},
            {"id": 3, "name": "查询失败", "poster_path": "/c.jpg"},
        ]

        async def imdb_lookup(_type, tmdb_id):
            if tmdb_id == 3:
                raise RuntimeError("TMDB timeout")
            return "tt0000001" if tmdb_id == 1 else None

        with patch.object(app, "imdb_id_for_tmdb", side_effect=imdb_lookup):
            series = await app.catalog_metas("series", "tv", results)
            movies = await app.catalog_metas("movie", "movie", results)

        self.assertEqual([m["id"] for m in series], ["tch:series:1:tt0000001", "tch:series:2"])
        self.assertEqual(
            [m["behaviorHints"]["defaultVideoId"] for m in movies],
            ["tt0000001", "tmdb:2"],
        )

    async def test_tmdb_only_series_uses_tmdb_episode_ids_and_skips_cinemeta(self):
        details = {"name": "中文剧", "seasons": [{"season_number": 1}]}
        season = {"episodes": [{"episode_number": 1, "name": "第一集", "overview": "简介"}]}
        cinemeta = AsyncMock()

        with (
            patch.object(app, "cache", app.SimpleTTLCache()),
            patch.object(app, "cinemeta_meta", new=cinemeta),
            patch.object(
                app,
                "get_chinese_bundle",
                new=AsyncMock(return_value=(details, "CN", {}, ["zh-CN"])),
            ),
            patch.object(app, "get_season_details", new=AsyncMock(return_value=season)),
        ):
            response = await app.metadata_proxy("series", "tch:series:777")

        meta = response["meta"]
        cinemeta.assert_not_awaited()
        self.assertEqual(meta["id"], "tch:series:777")
        self.assertEqual(meta["name"], "中文剧")
        self.assertEqual([v["id"] for v in meta["videos"]], ["tmdb:777:1:1"])

    async def test_tmdb_only_movie_streams_with_tmdb_id(self):
        with (
            patch.object(app, "cache", app.SimpleTTLCache()),
            patch.object(app, "cinemeta_meta", new=AsyncMock()),
            patch.object(
                app,
                "get_chinese_bundle",
                new=AsyncMock(return_value=({"title": "中文片"}, "HK", {}, ["zh-HK"])),
            ),
        ):
            response = await app.metadata_proxy("movie", "tch:movie:888")

        self.assertEqual(response["meta"]["behaviorHints"]["defaultVideoId"], "tmdb:888")


class SearchTests(unittest.IsolatedAsyncioTestCase):
    def test_country_catalogs_accept_optional_search(self):
        # Genre rows (donghua, variety) are subsets of cn_series: no search there.
        genre_rows = {"cn_donghua", "cn_variety"}
        for catalog in app.MANIFEST["catalogs"]:
            with self.subTest(catalog=catalog["id"]):
                search = [e for e in catalog["extra"] if e["name"] == "search"]
                expected = [] if catalog["id"] in genre_rows else [
                    {"name": "search", "isRequired": False}
                ]
                self.assertEqual(search, expected)

    def test_parse_search(self):
        self.assertEqual(app.parse_search("search=%E4%BB%99%E9%80%86.json"), "仙逆")
        self.assertEqual(app.parse_search("search=仙逆.json"), "仙逆")
        self.assertEqual(app.parse_search("search=  a   b &skip=20.json"), "a b")
        self.assertEqual(app.parse_search("genre=Latest.json"), "")
        self.assertEqual(app.parse_extra("search=x&skip=20.json"), (20, "Popular"))

    async def test_search_splits_results_into_cn_and_hk_catalogs(self):
        results = [
            {"id": 1, "title": "大陆片", "original_language": "zh", "poster_path": "/1.jpg"},
            {"id": 2, "title": "港片", "original_language": "cn", "poster_path": "/2.jpg"},
            {"id": 3, "title": "Hollywood", "original_language": "en", "poster_path": "/3.jpg"},
            {"id": 4, "title": "台湾片", "original_language": "zh", "poster_path": "/4.jpg"},
        ]
        countries = {
            1: {"production_countries": [{"iso_3166_1": "CN"}]},
            2: {"production_countries": [{"iso_3166_1": "HK"}, {"iso_3166_1": "CN"}]},
            4: {"production_countries": [{"iso_3166_1": "TW"}]},
        }

        async def details(_type, tmdb_id, _locale, _credits=False):
            return countries[tmdb_id]

        tmdb_get = AsyncMock(return_value={"results": results})
        with (
            patch.object(app, "cache", app.SimpleTTLCache()),
            patch.object(app, "tmdb_get", new=tmdb_get),
            patch.object(app, "get_details", side_effect=details) as get_details,
            patch.object(app, "imdb_id_for_tmdb", new=AsyncMock(return_value=None)),
        ):
            cn = await app.search_catalog("movie", "cn_movies", "片")
            hk = await app.search_catalog("movie", "hk_movies", "片")

        self.assertEqual([m["name"] for m in cn["metas"]], ["大陆片"])
        self.assertEqual([m["name"] for m in hk["metas"]], ["港片"])
        # The English-language result never costs a detail request.
        self.assertNotIn(3, [call.args[1] for call in get_details.call_args_list])
        self.assertEqual(tmdb_get.call_args.args[0], "/search/movie")
        self.assertEqual(tmdb_get.call_args.args[1]["query"], "片")

    def test_catalog_route_dispatches_search(self):
        search = AsyncMock(return_value={"metas": []})
        discover = AsyncMock(return_value={"metas": []})
        with (
            patch.object(app, "search_catalog", new=search),
            patch.object(app, "discover_catalog", new=discover),
            TestClient(app.app) as client,
        ):
            client.get("/catalog/series/cn_series/search=%E4%BB%99%E9%80%86.json")
            client.get("/catalog/series/cn_series/genre=Latest.json")

        search.assert_awaited_once_with("series", "cn_series", "仙逆", 0)
        discover.assert_awaited_once_with("series", "cn_series", 0, "Latest")


class ChineseLogoTests(unittest.IsolatedAsyncioTestCase):
    def test_pick_chinese_logo_prefers_region_then_png(self):
        images = {
            "logos": [
                {"iso_639_1": "en", "file_path": "/en.png", "vote_average": 9},
                {"iso_639_1": "zh", "iso_3166_1": "CN", "file_path": "/cn.png", "vote_average": 5},
                {"iso_639_1": "zh", "iso_3166_1": "HK", "file_path": "/hk.svg", "vote_average": 5},
                {"iso_639_1": "zh", "iso_3166_1": "HK", "file_path": "/hk.png", "vote_average": 1},
            ]
        }
        self.assertEqual(
            app.pick_chinese_logo(images, "CN"), "https://image.tmdb.org/t/p/w500/cn.png"
        )
        self.assertEqual(
            app.pick_chinese_logo(images, "HK"), "https://image.tmdb.org/t/p/w500/hk.png"
        )
        self.assertEqual(
            app.pick_chinese_logo({"logos": [{"iso_639_1": "zh", "file_path": "/x.svg"}]}, "CN"),
            "https://image.tmdb.org/t/p/original/x.svg",
        )
        self.assertIsNone(app.pick_chinese_logo({"logos": [images["logos"][0]]}, "CN"))
        self.assertIsNone(app.pick_chinese_logo(None, "CN"))

    async def meta_with_images(self, images):
        cinemeta = {
            "id": "tt1234567",
            "type": "movie",
            "name": "English",
            "logo": "https://images.metahub.space/logo/medium/tt1234567/img",
        }
        details = {"title": "中文片名", "images": images}
        with (
            patch.object(app, "cache", app.SimpleTTLCache()),
            patch.object(app, "cinemeta_meta", new=AsyncMock(return_value=cinemeta)),
            patch.object(
                app,
                "get_chinese_bundle",
                new=AsyncMock(return_value=(details, "CN", {}, ["zh-CN"])),
            ),
        ):
            return (await app.metadata_proxy("movie", "tch:movie:1:tt1234567"))["meta"]

    async def test_chinese_logo_replaces_cinemeta_english_logo(self):
        meta = await self.meta_with_images(
            {"logos": [{"iso_639_1": "zh", "iso_3166_1": "CN", "file_path": "/zh.png"}]}
        )
        self.assertEqual(meta["logo"], "https://image.tmdb.org/t/p/w500/zh.png")

    async def test_english_logo_removed_when_no_chinese_logo(self):
        meta = await self.meta_with_images({"logos": []})
        self.assertNotIn("logo", meta)

    async def test_detail_request_fetches_chinese_images_in_same_call(self):
        tmdb_get = AsyncMock(return_value={})
        with (
            patch.object(app, "cache", app.SimpleTTLCache()),
            patch.object(app, "tmdb_get", new=tmdb_get),
        ):
            await app.get_details("tv", 9, "zh-CN", True)

        params = tmdb_get.call_args.args[1]
        self.assertEqual(params["append_to_response"], "aggregate_credits,images,videos")
        self.assertEqual(params["include_image_language"], "zh")
        self.assertEqual(params["include_video_language"], "zh")

if __name__ == "__main__":
    unittest.main()
