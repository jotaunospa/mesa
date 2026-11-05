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
# El dashboard vive en main/web y se publica junto a los datos (Vercel sirve la rama datos).
cp -r web/. datos/
