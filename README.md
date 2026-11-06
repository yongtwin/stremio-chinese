# Chinese & HK Catalog for Stremio — v1.6.0

## v1.6.0

### New catalog rows (8 in total)

🇨🇳 中国电影 · 🇭🇰 香港电影 · 🇹🇼 台湾电影 · 🇨🇳 中国电视剧 · 🇭🇰 香港电视剧 ·
🇹🇼 台湾电视剧 · 🇨🇳 国产动画 (TMDB genre Animation) · 🇨🇳 综艺节目 (Reality or Talk).

Taiwanese titles now get Chinese metadata (zh-TW first, then zh-HK, zh-CN) and show up in
search. A title that lists several of these countries lands in one row: HK, then CN, then TW.
国产动画 and 综艺节目 are subsets of 中国电视剧, so they only offer sorting.

### Genre filter

The dropdown on each country row now lists Popular / Latest / Top Rated followed by genres
(悬疑, 喜剧, 科幻…). A genre lists that genre by popularity. Stremio's dropdown holds a
single value, so a genre and a sort order cannot be combined.

### Settings page: `/configure`

New installs go through `https://YOUR-PROJECT.vercel.app/configure`, where you choose:

- **TMDB API key (required).** A free v3 API key (32 characters) or a v4 Read Access Token
  (`eyJ…`) from <https://www.themoviedb.org/settings/api>. The page verifies it with TMDB
  directly from your browser; it never passes through this server for the check.
- **Script:** 按地区 (CN simplified, HK/TW traditional, the default), 简体优先 or 繁體優先.
  It shapes the language of catalog titles, search results and detail pages.
- **Catalogs:** which of the 8 rows to show.

The page then gives you a `stremio://` install link and an https URL. Your settings are
stored **in that URL's path** (base64url JSON, not encrypted), so the TMDB key is part of
the install link: keep it private and don't paste it in public places. The server doesn't
log or cache it. If you reconfigure, install the new link over the old one (same addon ID).

Stremio's Configure button on an installed addon reopens the page with your current choices.
A key TMDB rejects returns a clear error instead of silently using the server's key.

Installs from the plain `/manifest.json` (all existing users) keep working unchanged on the
server's key, and also get the four new rows. That URL just isn't advertised any more.

### Chinese posters and trailers

Detail pages use a Chinese-language poster when TMDB has one, and replace Cinemeta's trailers
with Chinese YouTube trailers/teasers when TMDB has any (otherwise the previous poster and
Cinemeta's trailers stay). Both come from the same TMDB request as credits and logos.

### Long series

The per-title limit rose from 20 to 60 seasons (e.g. 乡村爱情). Seasons are fetched in bulk,
one TMDB request per 20 seasons, instead of one request per season.

### Optional shared cache (Redis)

Vercel instances forget their in-memory cache whenever they restart, so a cold detail page
refetches everything from TMDB. To persist it:

1. Vercel → your project → **Storage** → create or connect an **Upstash Redis** database.
2. That sets `KV_REST_API_URL` and `KV_REST_API_TOKEN` (or `UPSTASH_REDIS_REST_URL` /
   `UPSTASH_REDIS_REST_TOKEN`) in the project.
3. Redeploy. `/health` then reports `"sharedCache": true`.

Without those variables nothing changes: the addon uses the in-memory cache only. Redis errors
never fail a request, and entries over 900 KB stay in memory only.

## v1.5.0

### Search by Chinese title

Every catalog now accepts Stremio's `search` extra, so typing 仙逆 or 無間道 in
Stremio's search bar returns results from this addon (TMDB search, localized
names). Results are split into the same four rows as the catalogs: a title lands
in 🇭🇰 if TMDB lists Hong Kong as a country, otherwise 🇨🇳 if it lists China.
Taiwanese and other titles are not shown.

### Titles without an IMDb ID are no longer hidden

Earlier versions dropped every TMDB title that had no IMDb ID, which hid many
mainland dramas and donghua. They now appear with:

| | Meta ID | Stream ID sent to stream addons |
| --- | --- | --- |
| With IMDb | `tch:series:<tmdb>:tt…` | `tt…` / `tt…:season:episode` |
| Without IMDb | `tch:series:<tmdb>` | `tmdb:<tmdb>` / `tmdb:<tmdb>:season:episode` |

AIOStreams' built-in debrid/TorBox addons accept `tmdb:` IDs. Stream addons that only
support IMDb (`tt`) IDs will not return sources for these titles; Stremio simply
doesn't ask them. Titles keep their existing IDs. If a lookup of the IMDb ID fails
temporarily, the title is skipped from that catalog page instead of being given a
second, different ID.

### Chinese title logos

The detail page used Cinemeta's English title art (e.g. "Renegade Immortal"). It
now uses TMDB's Chinese-language logo when one exists (traditional for HK titles,
simplified for CN titles), fetched in the same TMDB request as the credits. Without
a Chinese logo, the English one is removed and Stremio shows the Chinese name.

## v1.4.2: fix "No metadata was found!" on detail pages

Symptom: opening some catalog entries (e.g. 仙逆,
`#/detail/series/tch%3Aseries%3A223911%3Att27432264`) showed **No metadata was
found!**, **No videos found for this meta!**, and a bogus Season 0 / Episode
`<tmdb-id>` selector, even though the meta endpoint returned HTTP 200.

Two independent causes, both reproduced in Stremio Web:

1. **CDN-cached responses without CORS headers.** Starlette's `CORSMiddleware`
   only adds `Access-Control-Allow-Origin` when the request carries an `Origin`
   header. Meta/catalog responses are `Cache-Control: public`, so Vercel's CDN
   cached whichever copy came first. If that was a non-browser request (curl, a
   native app, a health check), every browser got a CORS-blocked copy for up to
   30 minutes. This is why it happened "sometimes". Fix: CORS headers are now sent
   on every response.
2. **Episode objects with both `title` and `name`.** Stremio Core declares
   `Video.title` with `#[serde(alias = "name")]`, so a video with both keys fails
   with a duplicate-field error, and that rejects the whole meta. Cinemeta episodes
   carry `name`; we added a Chinese `title`. Shows where Cinemeta has an episode
   list (仙逆, 嫁金钗, 完美世界, 凡人修仙传…) always failed. Shows without one
   (most HK dramas) worked. Fix: episodes now carry only `title` (with Cinemeta's
   `name` as the fallback text), and `stream`/`streams` are collapsed the same way.

The `tch:` custom ID format was **not** the problem; it renders fine once the
two issues above are fixed. Addon ID (`com.twin.chinese-hk-catalog.v141`) and all
meta/video IDs are unchanged, so saved links and library items keep working.

### Metadata vs. playback sources

This addon supplies only catalogs and metadata. A rendered Chinese detail page with
an episode list means metadata works. Whether **sources** appear after picking an
episode depends on your stream addons (AIOStreams/TorBox, etc.) having results for
the IMDb ID this addon hands them (`tt…` for movies, `tt…:season:episode` for
episodes). An empty source list on a rendered page is a stream-addon matter.

## v1.4.1

1. Series episodes are generated from TMDB even when Cinemeta has no episode list.
2. Locale fallbacks are checked when the preferred locale returns non-Chinese text.

## Why detail pages were English before

Earlier versions returned IMDb IDs (`tt...`) as the catalog/meta ID.

Stremio asks all relevant metadata addons for that ID. Cinemeta also owns IMDb IDs,
and Stremio's MetaDetails model uses the first successfully loaded meta item in addon
order. That meant Cinemeta could win and display English metadata even though this
addon also returned Chinese metadata.

## v1.4 architecture

Catalog item IDs now look like:

```text
tch:movie:123456:tt1234567
tch:series:654321:tt7654321
```

Only this addon declares the `tch:` meta prefix, so its Chinese detail page is used.

Playback remains compatible:

### Movies

The meta object sets:

```json
"behaviorHints": {
  "defaultVideoId": "tt1234567"
}
```

Stremio therefore requests streams using the IMDb ID, so AIOStreams/TorBox still work.

### Series

The meta ID is custom. Episodes are built from TMDB season data, with matching
Cinemeta fields retained when available. Episode video IDs use the standard IMDb format:

```text
tt7654321:1:1
tt7654321:1:2
```

Season 0 specials are excluded, and no more than 60 seasons are fetched for one detail
request (20 in versions before 1.6.0). Selecting an episode sends `tt...:season:episode` to stream addons, even when
Cinemeta has no episode metadata.

## Catalogs

- 🇨🇳 中国电影
- 🇭🇰 香港电影
- 🇹🇼 台湾电影
- 🇨🇳 中国电视剧
- 🇭🇰 香港电视剧
- 🇹🇼 台湾电视剧
- 🇨🇳 国产动画
- 🇨🇳 综艺节目

Sorting:
- Popular
- Latest
- Top Rated

Plus a genre list on the country rows (see v1.6.0).

## Language behavior

CN:
- zh-CN title/description
- Chinese genres
- Chinese/native cast/director/writer names when TMDB has them
- Chinese episode metadata when available

HK:
- zh-HK first
- zh-TW then zh-CN fallback
- Chinese/native cast/director/writer names when TMDB has them
- Chinese episode metadata when available

TW:
- zh-TW first
- zh-HK then zh-CN fallback

The `/configure` script setting overrides these: 简体优先 puts zh-CN first for every
title, 繁體優先 puts zh-HK (HK titles) or zh-TW (everything else) first.

Other normal Stremio catalogs:
- unchanged
- Cinemeta remains English

## Install URL

After deploying, open the settings page and follow it (see v1.6.0):

```text
https://YOUR-PROJECT.vercel.app/configure
```

The root `https://YOUR-PROJECT.vercel.app/manifest.json` and the legacy `/v140/` routes remain
available for existing installs. They use the server's `TMDB_API_TOKEN` and the default settings.

## VERY IMPORTANT: remove older copies

Stremio loads catalogs from every installed addon. If v1.0, v1.3, v1.3.1 or v1.3.2
are still installed, you will see duplicate 中国电影 / 香港电影 rows.

Uninstall every older "Chinese & HK Catalog" entry and keep only:

```text
Chinese & HK Catalog v1.6.0
```

Upgrading from any v1.4.x or v1.5.x needs no reinstall: the addon ID is the same and Stremio
refreshes the manifest from the same URL. If an old copy with ID
`com.twin.chinese-hk-catalog.v140` is installed, uninstall it.

New rows, the genre list and search are declared in the manifest, so they appear once
Stremio refreshes its stored copy. To get them immediately, open the install URL
again and click Install (or Update); library and watch history are kept.

## Update on Vercel

1. Replace your GitHub repo files with this version.
2. Commit/push.
3. Vercel redeploys automatically.
4. Your existing `TMDB_API_TOKEN` remains unchanged.
5. Verify:

```text
https://YOUR-PROJECT.vercel.app/health
```

should show:

```json
"version": "1.6.0"
```

Then verify:

```text
https://YOUR-PROJECT.vercel.app/manifest.json
```

shows:

```json
"id": "com.twin.chinese-hk-catalog.v141",
"version": "1.6.0"
```

Deploying purges Vercel's CDN cache, which also clears any CORS-less copies cached
before v1.4.2. To confirm the CORS fix, request a meta URL **without** an Origin
header and check that `access-control-allow-origin: *` is still present:

```bash
curl -sI "https://YOUR-PROJECT.vercel.app/meta/series/tch%3Aseries%3A223911%3Att27432264.json"
```

## Tests

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v
```

`StremioClientCompatibilityTests` mirrors Stremio Core's `Video` deserialization
rules and the CORS-without-Origin case.

## TMDB notice

This product uses the TMDB API but is not endorsed or certified by TMDB.
