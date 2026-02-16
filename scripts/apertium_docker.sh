#!/usr/bin/env bash
set -euo pipefail

# Wrapper para ejecutar Apertium dentro de un contenedor Docker.
# Usa la imagen oficial `apertium/apertium` por defecto.

IMAGE="${APERTIUM_IMAGE:-apertium-en-es:local}"

# Construye comando seguro para bash -lc dentro del contenedor
CMD=("apertium" "-d" "/usr/share/apertium")
for arg in "$@"; do
  CMD+=("$arg")
done

exec docker run --rm -i --entrypoint /bin/bash "$IMAGE" -lc "cat > /tmp/in.txt && $(printf '%q ' "${CMD[@]}") /tmp/in.txt"
