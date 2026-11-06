# Chinese & HK Catalog for Stremio

[![Version](https://img.shields.io/badge/version-1.6.4-5b45ff)](#changelog)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Support on Ko-fi](https://img.shields.io/badge/Ko--fi-support-13c3ff?logo=ko-fi&logoColor=white)](https://ko-fi.com/twin_chan)

A [Stremio](https://www.stremio.com/) addon that adds China, Hong Kong and Taiwan movie and TV
catalogs with **Chinese metadata**: titles, descriptions, cast, genres, posters, logos, trailers
and episode names. Everything else in Stremio stays as it is.

Catalogs and metadata only. This addon does **not** provide streams: playback still goes through
your own stream addons (AIOStreams, TorBox, …), which receive the usual IMDb IDs.

**Hosted instance:** <https://stremio-chinese.vercel.app> · **Self-hosting:** see [Deploy your own](#deploy-your-own).

## ❤️ Support This Project

This addon is developed and maintained independently.

If you find it useful, consider buying me a coffee! Your support helps cover hosting costs, maintenance, and future improvements.

☕ **[Support me on Ko-fi](https://ko-fi.com/twin_chan)**

Thank you for supporting independent open-source development!

## Contents

- [Features](#features)
- [Install](#install)
- [Configuration](#configuration)
- [Catalogs](#catalogs)
- [Language behavior](#language-behavior)
- [How it works](#how-it-works)
- [Deploy your own](#deploy-your-own)
- [Development](#development)
- [Changelog](#changelog)
- [License and notices](#license-and-notices)

## Features

- **Eight catalog rows:** 🇨🇳 中国电影 · 🇭🇰 香港电影 · 🇹🇼 台湾电影 · 🇨🇳 中国电视剧 · 🇭🇰 香港电视剧 ·
  🇹🇼 台湾电视剧 · 🇨🇳 国产动画 · 🇨🇳 综艺节目
- **Genre and Sort** that combine: e.g. 香港电影 → 喜剧 → Latest (two dropdowns on Stremio Web, "喜剧 · Latest" entries on mobile)
- **Search by Chinese title:** type 仙逆 or 無間道 in Stremio's search bar
- **Chinese detail pages:** title, overview, genres, cast, directors and writers (native names
  where TMDB has them), poster, title logo, trailers and per-episode titles
- **Long series:** up to 60 seasons per title (e.g. 乡村爱情), fetched in bulk
- **Titles without an IMDb ID** (many mainland dramas and donghua) are included
- **Per-install settings:** simplified or traditional script, which rows to show, your own TMDB key
- **Optional shared cache** (Upstash Redis) so cold starts don't refetch everything

## Install

Open the settings page and follow it:

```text
https://stremio-chinese.vercel.app/configure
```

1. Paste your TMDB API key (see [Configuration](#configuration)).
2. Choose the script and the catalogs you want.
3. Click **Generate install link**, then **Install in Stremio** (or paste the https URL into
   Stremio's addon search box).

**Remove older copies first.** Stremio loads catalogs from every installed addon, so if v1.0, v1.3,
v1.3.1 or v1.3.2 are still installed you will see duplicate 中国电影 / 香港电影 rows. Uninstall every
older "Chinese & HK Catalog" entry (including the one with ID `com.twin.chinese-hk-catalog.v140`)
and keep only one.

**Already installed from `/manifest.json`?** It keeps working unchanged on the server's TMDB key,
and you get the new rows automatically. No reinstall is needed: the addon ID is the same, and Stremio
refreshes the manifest from the same URL. To see new rows immediately, open the install URL again and
click Install (or Update). Your library and watch history are kept.

## Configuration

The settings page (`/configure`) sets three things:

| Setting | Options |
| --- | --- |
| **TMDB API key** (required) | A free v3 API key (32 characters) or a v4 Read Access Token (`eyJ…`) from [themoviedb.org/settings/api](https://www.themoviedb.org/settings/api) |
| **Script** | 按地区 (default: CN simplified, HK/TW traditional) · 简体中文优先 · 繁體中文優先 |
| **Catalogs** | Any of the 8 rows |

The page checks your key with TMDB directly from your browser, so the check never passes through
this server. A key that TMDB rejects later returns a clear error; it never silently falls back to the
server's key.

> **Privacy note:** your settings are stored **in the install URL's path** (base64url JSON, not
> encrypted), so the TMDB key is part of the link. Keep the link private. The server does not log
> or cache the key. To change settings, generate a new link and install it over the old one, or use
> the **Configure** button on the installed addon in Stremio, which reopens the page with your
> current choices.

## Catalogs

| Row | TMDB source |
| --- | --- |
| 🇨🇳 中国电影 / 🇨🇳 中国电视剧 | Origin country CN |
| 🇭🇰 香港电影 / 🇭🇰 香港电视剧 | Origin country HK |
| 🇹🇼 台湾电影 / 🇹🇼 台湾电视剧 | Origin country TW |
| 🇨🇳 国产动画 | CN series, genre Animation |
| 🇨🇳 综艺节目 | CN series, genre Reality or Talk |

Each country row has two separate dropdowns in Stremio's Discover page, which keep their choices
independently so you can combine them:

- **Sort:** Popular (default), Latest or Top Rated. Top Rated only counts titles with at least 50 votes,
  so a narrow combination (e.g. 香港电影 → 恐怖 → Top Rated) can return few titles.
- **Genre:** 悬疑, 喜剧, 科幻, … (the list differs for movies and series). Leave it empty for all genres.

On desktop, Stremio Web shows the first three dropdowns (type, catalog, then the first filter) in the
row and keeps further filters behind the filter-icon button, so **Sort is the dropdown in the row and
Genre is under the filter button**. Narrow browser windows put the filters in a Filters sheet.

**Stremio mobile apps** only draw a filter named `genre` and ignore any other, so the Genre list also
holds the sort orders and "genre · sort" entries:

```text
Popular · Latest · Top Rated · 剧情 · 剧情 · Latest · 剧情 · Top Rated · 喜剧 · 喜剧 · Latest · …
```

Pick `喜剧 · Latest` for comedies newest first, or `Latest` alone for all genres. On Stremio Web the
same entries appear in the Genre list too, which is redundant next to the Sort dropdown; when Sort is
set it wins over the sort inside a Genre entry. 国产动画 and 综艺节目 list the three sort orders only.

国产动画 and 综艺节目 are subsets of 中国电视剧, so they offer the Sort dropdown only (no genre, no
search).

A title that lists several of these countries lands in one row: HK first, then CN, then TW. Titles
from other countries are not shown here, and the rest of Stremio keeps Cinemeta's English metadata.

## Language behavior

| Title origin | Locale order |
| --- | --- |
| CN | zh-CN, then zh-TW, zh-HK |
| HK | zh-HK, then zh-TW, zh-CN |
| TW | zh-TW, then zh-HK, zh-CN |

The script setting overrides this: **简体优先** puts zh-CN first for every title; **繁體優先** puts
zh-HK (HK titles) or zh-TW (everything else) first. Cast, director and writer names use the native
Chinese name when TMDB has one.

## How it works

### Custom meta IDs

Catalog items use addon-specific IDs so Cinemeta cannot win the detail-page selection for the same
IMDb ID (Stremio uses the first meta provider that loads):

| | Meta ID | ID sent to stream addons |
| --- | --- | --- |
| With IMDb | `tch:series:<tmdb>:tt…` | `tt…` (episodes: `tt…:season:episode`) |
| Without IMDb | `tch:series:<tmdb>` | `tmdb:<tmdb>` (episodes: `tmdb:<tmdb>:season:episode`) |

Movies set `behaviorHints.defaultVideoId` to the IMDb (or `tmdb:`) ID, so AIOStreams/TorBox work as
before. AIOStreams' built-in debrid/TorBox addons accept `tmdb:` IDs; stream addons that only support
IMDb IDs return nothing for those titles, and Stremio simply doesn't ask them. If a lookup of the IMDb
ID fails temporarily, the title is skipped from that catalog page instead of getting a second,
different ID.

### Series episodes

Episodes are built from TMDB season data, keeping matching Cinemeta fields when available. Season 0
specials are excluded, and at most 60 seasons are fetched per title (one TMDB request per 20 seasons).

### Posters, logos and trailers

All of these come from the same TMDB request as the credits. A Chinese-language poster and title logo
replace the defaults when TMDB has them (otherwise the English logo is dropped so Stremio shows the
Chinese name). Chinese YouTube trailers and teasers replace Cinemeta's when TMDB has any.

### Metadata vs. playback sources

A rendered Chinese detail page with an episode list means metadata works. Whether **sources** appear
after picking an episode depends on your stream addons having results for the ID this addon hands them.
An empty source list on a rendered page is a stream-addon matter.

## Deploy your own

The app is a single FastAPI file (`app.py`) that runs on Vercel.

1. Fork or clone this repository and import it in Vercel.
2. Set an environment variable (see [`.env.example`](.env.example)):
   - `TMDB_API_TOKEN` (recommended, a v4 Read Access Token), or
   - `TMDB_API_KEY` (a classic v3 key).

   This is the fallback key for installs made from the plain `/manifest.json`. Installs made on
   `/configure` use their own key.
3. Deploy. Pushes to `main` redeploy automatically.
4. Verify:

   ```text
   https://YOUR-PROJECT.vercel.app/health
   https://YOUR-PROJECT.vercel.app/manifest.json
   ```

   `/health` should report the current `version`; the manifest keeps the ID
   `com.twin.chinese-hk-catalog.v141`.

### Optional: shared cache (Redis)

Vercel instances forget their in-memory cache whenever they restart, so a cold detail page refetches
everything from TMDB. To persist it:

1. In Vercel, open your project → **Storage** → create or connect an **Upstash Redis** database.
2. That sets `KV_REST_API_URL` and `KV_REST_API_TOKEN` (or `UPSTASH_REDIS_REST_URL` and
   `UPSTASH_REDIS_REST_TOKEN`) in the project.
3. Redeploy. `/health` then reports `"sharedCache": true`.

Without those variables the addon uses its in-memory cache only. Redis errors never fail a request,
and entries over 900 KB stay in memory only.

### CORS and CDN caching

Meta and catalog responses are `Cache-Control: public`, and the CDN caches whichever copy it sees
first. CORS headers are therefore sent on **every** response, not only when an `Origin` header is
present. To confirm after a deploy, request a meta URL without an Origin header and look for
`access-control-allow-origin: *`:

```bash
curl -s -D - -o /dev/null "https://YOUR-PROJECT.vercel.app/meta/series/tch%3Aseries%3A223911%3Att27432264.json"
```

## Development

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v
```

`StremioClientCompatibilityTests` mirrors Stremio Core's `Video` deserialization rules and the
CORS-without-Origin case. To run the addon locally, set a TMDB key and start it with
`uvicorn app:app --port 8000`.

## Changelog

### 1.6.4

- **Genre + sort on the mobile apps.** They only show a filter named `genre`, so 1.6.2/1.6.3's separate
  Sort dropdown was invisible there and mobile lost sorting. The Genre list now also carries `Popular`,
  `Latest`, `Top Rated` and "genre · Latest" / "genre · Top Rated" entries for every genre (45 per row).
  Stremio Web keeps its separate Sort dropdown. An explicit Sort wins over a sort inside a Genre entry.
  国产动画 and 综艺节目 get a Genre list of the sort orders only.

### 1.6.3

- **Sort and Genre swapped.** Stremio Web only shows the first three dropdowns (type, catalog, first
  filter) in the row and hides the rest behind the filter-icon button. Genre came first in 1.6.2, which
  pushed Sort behind the icon. Sort now comes first, so it is a visible dropdown and Genre is under the
  filter button. No request format changed.

### 1.6.2

- **Separate Genre and Sort dropdowns.** Before, one dropdown held both the sort orders and the genres,
  so you could pick only one and a genre was always listed by popularity. Genre and Sort are now two
  filters that combine, e.g. 香港电影 → 喜剧 → Latest.
- Old links and clients still on the previous manifest (`genre=Latest`) keep working: a sort name sent
  as the genre is read as the sort.

### 1.6.1

- Ko-fi support link on the home page, the `/configure` page and the manifest description (which
  Stremio shows in its install dialog). A plain link: no widget, no third-party script, no tracking.

### 1.6.0

- **Four new rows:** 台湾电影, 台湾电视剧, 国产动画 and 综艺节目 (8 rows in total). Taiwanese titles get
  Chinese metadata and appear in search.
- **Genre filter** in each country row's dropdown.
- **`/configure` page:** script preference, catalog selection and a required per-install TMDB key.
  Installs from the plain `/manifest.json` keep working on the server's key.
- **Chinese posters and trailers** on detail pages.
- **Season limit** raised from 20 to 60, fetched in bulk.
- **Optional Redis cache** (see [Deploy your own](#deploy-your-own)).

### 1.5.0

- **Search by Chinese title** on every catalog row. A title lands in 🇭🇰 if TMDB lists Hong Kong,
  otherwise 🇨🇳 if it lists China (Taiwan joined in 1.6.0).
- **Titles without an IMDb ID** are no longer hidden. They use `tch:<type>:<tmdb>` meta IDs and
  `tmdb:<id>` stream IDs.
- **Chinese title logos** replace Cinemeta's English title art.

### 1.4.2 — fix "No metadata was found!"

Opening some entries (e.g. 仙逆, `#/detail/series/tch%3Aseries%3A223911%3Att27432264`) showed **No
metadata was found!** and a bogus Season 0 selector even though the meta endpoint returned HTTP 200.
Two independent causes, both reproduced in Stremio Web:

1. **CDN-cached responses without CORS headers.** Starlette's `CORSMiddleware` only adds
   `Access-Control-Allow-Origin` when the request carries an `Origin` header. Vercel's CDN cached
   whichever copy came first, so a first request from curl or a health check left every browser with a
   CORS-blocked copy for up to 30 minutes. Fix: CORS headers on every response.
2. **Episode objects with both `title` and `name`.** Stremio Core declares `Video.title` with
   `#[serde(alias = "name")]`, so a video carrying both keys fails with a duplicate-field error and
   rejects the whole meta. Cinemeta episodes carry `name`; we had added a Chinese `title`. Fix:
   episodes carry only `title` (with Cinemeta's `name` as fallback text), and `stream`/`streams` are
   collapsed the same way.

The `tch:` ID format was not the problem. The addon ID and all meta/video IDs are unchanged, so saved
links and library items keep working.

### 1.4.1

- Series episodes are generated from TMDB even when Cinemeta has no episode list.
- Locale fallbacks are checked when the preferred locale returns non-Chinese text.

### 1.4.0 — why detail pages used to be English

Earlier versions used IMDb IDs (`tt…`) as the catalog/meta ID. Stremio asks every relevant meta addon
for that ID, and Cinemeta also owns IMDb IDs. Stremio's detail view uses the first meta that loads in
addon order, so Cinemeta could win and show English metadata even though this addon also returned
Chinese. 1.4 introduced the `tch:` meta IDs described under [How it works](#how-it-works).

## License and notices

Released under the [MIT License](LICENSE).

This product uses the TMDB API but is not endorsed or certified by TMDB.
