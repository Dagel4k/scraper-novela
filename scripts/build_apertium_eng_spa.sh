#!/usr/bin/env bash
set -euo pipefail

# Construye una imagen de Apertium con el par eng-spa ya instalado.

IMG_NAME="apertium-en-es:local"
docker build --pull -t "$IMG_NAME" -f Dockerfile.apertium-eng-spa .
echo "\n[ok] Imagen construida: $IMG_NAME"
echo "Para usarla: export APERTIUM_IMAGE=$IMG_NAME"
