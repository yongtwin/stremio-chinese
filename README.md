# Chinese & HK Catalog for Stremio — v1.4.1

This version fixes the two issues seen in earlier builds:

1. Duplicate China/HK rows caused by having multiple old versions installed.
2. Chinese catalog items opening an English Cinemeta detail page.

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

The meta ID is custom, but episode video IDs stay in Cinemeta's standard format:

```text
tt7654321:1:1
tt7654321:1:2
```

Selecting an episode therefore still sends the normal IMDb episode ID to stream addons.

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
https://YOUR-PROJECT.vercel.app/v141/manifest.json
```

## VERY IMPORTANT: remove older copies

Stremio loads catalogs from every installed addon. If v1.0, v1.3, v1.3.1 or v1.3.2
are still installed, you will see duplicate 中国电影 / 香港电影 rows.

Uninstall every older "Chinese & HK Catalog" entry and keep only:

```text
Chinese & HK Catalog v1.4.1
```

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
"version": "1.4.0"
```

Then verify:

```text
https://YOUR-PROJECT.vercel.app/v141/manifest.json
```

shows:

```json
"id": "com.twin.chinese-hk-catalog.v141",
"version": "1.4.0"
```

## TMDB notice

This product uses the TMDB API but is not endorsed or certified by TMDB.


## v1.4.1 — series stream fix

Earlier builds localized only the episode objects already supplied by Cinemeta.
Some China/Hong Kong series have incomplete or missing episode lists in Cinemeta,
so the detail page could show no usable episode stream IDs.

v1.4.1 builds the episode list directly from TMDB and assigns every episode the
canonical Stremio/AIOStreams IMDb ID format:

```text
ttSERIESIMDBID:season:episode
```

Example:

```text
tt1234567:1:1
tt1234567:1:2
```

This keeps the custom `tch:` parent meta ID for Chinese detail metadata while
sending normal IMDb episode IDs to AIOStreams/TorBox for playback.
