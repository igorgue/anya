# Anya Website

This site lives in `website/` and is built with Next.js static export.

## Local development

```bash
cd website
npm install
npm run dev
```

## Production build

```bash
cd website
npm run build
```

The static site is emitted to `website/out`.

## GitHub Pages

This repo deploys the `website/` app to GitHub Pages from GitHub Actions.
The workflow runs from the repository root, installs dependencies in `website/`,
builds the static export, and uploads `website/out` as the Pages artifact.

When running in GitHub Actions, `next.config.mjs` automatically sets:
- `basePath` to `/<repo-name>`
- `assetPrefix` to `/<repo-name>`

That makes it work on Pages without needing a custom domain.
