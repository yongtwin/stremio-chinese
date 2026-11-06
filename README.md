# Chinese & HK Catalog for Stremio — v1.5.0

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

Season 0 specials are excluded, and no more than 20 seasons are fetched for one detail
request. Selecting an episode sends `tt...:season:episode` to stream addons, even when
Cinemeta has no episode metadata.

## Catalogs

- 🇨🇳 中国电影
- 🇭🇰 香港电影
- 🇨🇳 中国电视剧
- 🇭🇰 香港电视剧

Sorting:
- Popular
- Latest
- Top Rated

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

Other normal Stremio catalogs:
- unchanged
- Cinemeta remains English

## Install URL

After deploying:

```text
https://YOUR-PROJECT.vercel.app/manifest.json
```

The legacy `/v140/` routes remain available for existing installs; use the stable root
manifest URL for new installs and updates.

## VERY IMPORTANT: remove older copies

Stremio loads catalogs from every installed addon. If v1.0, v1.3, v1.3.1 or v1.3.2
are still installed, you will see duplicate 中国电影 / 香港电影 rows.

Uninstall every older "Chinese & HK Catalog" entry and keep only:

```text
Chinese & HK Catalog v1.5.0
```

Upgrading from any v1.4.x needs no reinstall: the addon ID is the same and Stremio
refreshes the manifest from the same URL. If an old copy with ID
`com.twin.chinese-hk-catalog.v140` is installed, uninstall it.

Search (new in v1.5.0) is declared in the manifest, so it starts working once
Stremio refreshes its stored copy. To get it immediately, open the install URL
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
"version": "1.5.0"
```

Then verify:

```text
https://YOUR-PROJECT.vercel.app/manifest.json
```

shows:

```json
"id": "com.twin.chinese-hk-catalog.v141",
"version": "1.5.0"
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
