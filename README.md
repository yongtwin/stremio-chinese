# Chinese & HK Catalog for Stremio — v1.3

A small FastAPI Stremio catalog + metadata addon designed for a mixed-language Stremio setup.

## Behaviour

### China / Hong Kong titles

- Chinese title
- Chinese description
- Chinese genres
- Cast / director / writer names use native Chinese names when TMDB has them
- TV episode title + episode description are localized when TMDB has Chinese episode metadata
- IMDb/Cinemeta IDs are preserved so AIOStreams/TorBox can still resolve streams

### Everything else

The addon falls back to Cinemeta's normal English metadata.

## Catalogs

- 🇨🇳 中国电影
- 🇭🇰 香港电影
- 🇨🇳 中国电视剧
- 🇭🇰 香港电视剧

Sorting options:

- Popular
- Latest
- Top Rated

The selector uses Stremio's `genre` extra slot because Stremio does not expose a portable generic sort dropdown for addon catalogs.

## v1.3 changes

### Better Chinese actor/director/writer names

TMDB does not reliably localize person names using the normal `language` parameter.

v1.3 uses this order:

1. Chinese `original_name`
2. Chinese `name`
3. Chinese alias from the person's `also_known_as`
4. TMDB's normal name as fallback

Person alias results are cached for 30 days.

### Chinese TV episode metadata

For CN/HK series, the addon keeps Cinemeta's episode IDs, for example:

```text
tt1234567:1:1
```

but overlays TMDB Chinese episode metadata when available:

- title
- overview
- thumbnail
- air date
- runtime

That means existing stream addons can continue using the same episode IDs.

### Fewer TMDB requests

The preferred localized movie/TV detail request now uses TMDB `append_to_response`
to include credits (or aggregate TV credits) in the same request.

Fallback Chinese locales are requested only when the preferred locale is missing
the title or overview.

### TMDB attribution

The root page includes the required notice:

> This product uses the TMDB API but is not endorsed or certified by TMDB.

## Deploy/update on Vercel

If you already deployed an older version:

1. Replace the repo files with this version.
2. Commit and push to GitHub.
3. Vercel redeploys automatically.
4. Your existing `TMDB_API_TOKEN` remains configured.
5. Open:

```text
https://YOUR-PROJECT.vercel.app/health
```

You should see:

```json
"version": "1.3.0"
```

If Stremio keeps an old cached manifest, remove and reinstall the same manifest URL.

## Environment variable

Preferred:

```text
TMDB_API_TOKEN=your_tmdb_read_access_token
```

Alternative:

```text
TMDB_API_KEY=your_v3_api_key
```

## Install in Stremio

```text
https://YOUR-PROJECT.vercel.app/manifest.json
```

## Useful test endpoints

```text
/health
/manifest.json

/catalog/movie/cn_movies.json
/catalog/movie/hk_movies.json
/catalog/series/cn_series.json
/catalog/series/hk_series.json

/catalog/movie/hk_movies/genre=Latest.json
/catalog/movie/hk_movies/genre=Top%20Rated.json

/meta/movie/tt....json
/meta/series/tt....json
```

## Local run

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# Linux/macOS
source .venv/bin/activate

pip install -r requirements.txt

# PowerShell
$env:TMDB_API_TOKEN="your_token"

# Linux/macOS
export TMDB_API_TOKEN="your_token"

uvicorn app:app --reload --port 8000
```

## Notes

- The addon intentionally skips catalog entries without an IMDb ID to preserve compatibility with existing stream addons.
- Person names are not machine-translated. If TMDB has no native Chinese name/alias, the existing name is retained.
- Episode localization depends on TMDB having Chinese metadata for that season/episode.
- A defensive 20-season limit is used when enriching a single series detail request on serverless hosting.
- In-memory cache can be lost on Vercel cold starts; this is expected.
- No TorBox credential is stored or used by this addon.
