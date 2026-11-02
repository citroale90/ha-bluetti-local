#!/usr/bin/env bash
# Esegue i test nell'immagine Home Assistant già presente sul computer.
# Nessun accesso LAN, dispositivo o cartella della configurazione in uso.
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_IMAGE="${HA_IMAGE:-}"
if [[ -z "$TEST_IMAGE" ]]; then
  TEST_IMAGE="$(docker inspect home-assistant --format '{{.Image}}')"
fi
# Verifica locale: lo script non scarica o aggiorna immagini.
docker image inspect "$TEST_IMAGE" >/dev/null

docker run --rm \
  --network none --read-only \
  --user "$(id -u):$(id -g)" \
  --tmpfs /tmp:rw,nosuid,noexec,size=128m \
  --cap-drop ALL --security-opt no-new-privileges \
  --memory 768m --cpus 1 --pids-limit 128 \
  --label homelab.notify=false \
  --label com.docker.compose.oneoff=True \
  --entrypoint python \
  -v "$PROJECT_DIR:/audit:ro" \
  "$TEST_IMAGE" -B -c \
  "import sys, unittest; sys.path[:0] = ['/audit', '/audit/tests']; result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover('/audit/tests', pattern='test_*.py')); sys.exit(not result.wasSuccessful())"
