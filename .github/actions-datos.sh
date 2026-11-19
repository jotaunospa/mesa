#!/usr/bin/env bash
# Prepara ./datos como clon de la rama `datos` (la crea si no existe).
set -e
URL="https://x-access-token:${GH_TOKEN}@github.com/${GITHUB_REPOSITORY}"
if git ls-remote --exit-code "$URL" refs/heads/datos >/dev/null 2>&1; then
  git clone -q --depth 50 --branch datos --single-branch "$URL" datos
else
  mkdir -p datos && git -C datos init -q -b datos && git -C datos remote add origin "$URL"
fi
git -C datos config user.name "mesa-bot"
git -C datos config user.email "mesa-bot@users.noreply.github.com"
# La web ya no vive en datos: Vercel publica main/web (rama de producción main, carpeta raíz web) y solo
# cuando cambia la página. datos/web/vercel.json le dice a Vercel que no despliegue nada de esta rama.
if [ ! -f datos/web/vercel.json ]; then
  mkdir -p datos/web && printf '{\n  "git": {"deploymentEnabled": {"datos": false}}\n}\n' > datos/web/vercel.json
fi
