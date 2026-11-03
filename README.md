# Machine Morning Briefing v0.7 — RSS collector

**Existing `index.html` and Cloudflare `worker.js` are NOT modified.** This is an additive prototype.

## Installation
1. Copy `collector.py`, `feeds.json`, `requirements.txt`, `keywords.example.json` and `.github/workflows/morning.yml` into your existing GitHub repository, preserving paths.
2. In repository Settings > Actions > General, enable read/write workflow permissions (if needed). GitHub Pages should already be configured.
3. In Actions, run `Machine Morning Briefing RSS` using **Run workflow**.
4. Visit `https://machine-g.github.io/machine-morning-briefing/morning.html` after successful workflow completion.

## Keywords
The collector reads `keywords.json` when present. If absent, it falls back to `keywords.example.json` and **does not read the Cloudflare KV**. This means it will NOT yet use the 16 live cloud-synced keywords. Export keywords manually to `keywords.json` for testing. Do not commit tokens, sessions or private configuration.

## Caveats
- RSS feed URLs may change or stop responding; warnings are shown in GitHub Actions logs.
- Feed excerpts are *not* AI-generated Japanese summaries; many source excerpts will be in English.
- This starter has no images or ChatGPT notification integration.
- The workflow schedule may be delayed by GitHub Actions. The site is publicly readable.
- Review third-party content licenses before adding photos.
- To connect cloud keywords later, implement a **separate server-to-server read-only endpoint** with proper authentication; do not put an admin bearer token in a public GitHub Pages page or a public repo.
