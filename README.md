# Chinese & HK Catalog for Stremio

A tiny FastAPI Stremio addon for:

- 🇨🇳 中国电影
- 🇭🇰 香港电影
- 🇨🇳 中国电视剧
- 🇭🇰 香港电视剧

It uses TMDB for China/Hong Kong discovery and localized Chinese titles/descriptions.

It **does not provide streams**. Keep using AIOStreams / TorBox / your normal stream addons.

## What it does

- China catalogs use `zh-CN`
- Hong Kong catalogs prefer `zh-HK`, with Chinese fallbacks
- Catalog items are converted to IMDb `tt...` IDs, so normal Stremio stream addons can still resolve them
- Metadata acts as a proxy:
  - CN/HK titles → Chinese title + Chinese description
  - everything else → Cinemeta's normal English metadata
- For series, Cinemeta's episode/video IDs are preserved so existing stream addons remain compatible

## 1. Get a TMDB token

Create/log in to a TMDB account and obtain an API **Read Access Token**.

Recommended environment variable:

```text
TMDB_API_TOKEN=your_tmdb_read_access_token
```

A classic v3 API key is also supported:

```text
TMDB_API_KEY=your_v3_api_key
```

You only need one of them.

## 2. Deploy to Vercel

### Easiest: GitHub + Vercel

1. Extract this ZIP.
2. Create a GitHub repository and upload/push these files.
3. In Vercel, choose **Add New → Project** and import that GitHub repository.
4. In **Environment Variables**, add:
   - Name: `TMDB_API_TOKEN`
   - Value: your TMDB Read Access Token
5. Deploy.

Vercel supports FastAPI directly; no Docker or `vercel.json` is required.

### Or: Vercel CLI

After extracting:

```bash
npm i -g vercel
vercel
```

Add `TMDB_API_TOKEN` in the Vercel project settings, then redeploy.

## 3. Test it

Open:

```text
https://YOUR-PROJECT.vercel.app/
```

The page should show:

```text
✅ TMDB configured
```

Then test:

```text
https://YOUR-PROJECT.vercel.app/manifest.json
https://YOUR-PROJECT.vercel.app/catalog/movie/hk_movies.json
https://YOUR-PROJECT.vercel.app/catalog/movie/cn_movies.json
```

## 4. Install in Stremio

Use:

```text
https://YOUR-PROJECT.vercel.app/manifest.json
```

Or open the project root and click **Install in Stremio**.

## Important: metadata priority

Stremio's built-in Cinemeta is also a metadata provider for IMDb IDs.

This addon intentionally proxies Cinemeta:
- China/HK → overlays Chinese title/description from TMDB
- other countries → leaves Cinemeta metadata in English

For the Chinese detail page to win consistently, this addon needs to be the metadata provider that Stremio prioritizes. If your Stremio setup always prioritizes Cinemeta, you may need to use your existing addon-management/reordering setup to put this metadata addon before Cinemeta, or disable only Cinemeta metadata while keeping a metadata provider available.

You do **not** need to change AIOStreams/TorBox.

## Local run

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# Linux/macOS
source .venv/bin/activate

pip install -r requirements.txt

# Windows PowerShell
$env:TMDB_API_TOKEN="your_token"

# Linux/macOS
export TMDB_API_TOKEN="your_token"

uvicorn app:app --reload --port 8000
```

Open:

```text
http://127.0.0.1:8000/
```

## Endpoints

```text
/manifest.json

/catalog/movie/cn_movies.json
/catalog/movie/hk_movies.json
/catalog/series/cn_series.json
/catalog/series/hk_series.json

/meta/movie/tt....json
/meta/series/tt....json

/health
```

## Notes

- Catalogs are sorted by TMDB popularity.
- Entries without an IMDb ID are intentionally skipped for better compatibility with existing stream addons.
- In-memory caching is used. On Vercel, a cold function may lose its cache; this is expected.
- No TorBox key is stored or used by this project.
