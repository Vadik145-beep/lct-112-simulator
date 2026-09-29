#!/usr/bin/env bash
# Установка из поставки без интернета (docs/INSTALL.md, раздел 6). Запускается из каталога
# поставки, собранной scripts/offline_bundle.sh:
#
#   ./install.sh [--dir /opt/lct/app] [--profile ai] [--profile telephony] [--host АДРЕС]
#
# Что делает: проверяет контрольные суммы, загружает образы в Docker, распаковывает исходники,
# модели и материалы заказчика, создаёт .env из .env.example (если его ещё нет: свой
# SECRET_KEY, адрес сервера в сертификате и телефонии) и поднимает систему с запретом на
# скачивание чего-либо (--pull never, без сборки). Ничего из сети не нужно.
# Без --profile поднимается то же, что на стенде: ai (если в поставке есть модели) и telephony.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
TARGET="/opt/lct/app"
PROFILES=()
HOST_ADDR=""
while [ $# -gt 0 ]; do
  case "$1" in
    --dir) TARGET="$2"; shift ;;
    --profile) PROFILES+=(--profile "$2"); shift ;;
    --host) HOST_ADDR="$2"; shift ;;
    *) echo "неизвестный параметр: $1" >&2; exit 2 ;;
  esac
  shift
done

command -v docker >/dev/null || {
  echo "Docker не найден. Установите Docker Engine и docker compose v2 (https://docs.docker.com/engine/install/)," >&2
  echo "для контура без интернета — из пакетов .deb/.rpm вашего дистрибутива, затем запустите ./install.sh снова." >&2
  exit 1
}
docker compose version >/dev/null 2>&1 || { echo "нужен docker compose v2 (пакет docker-compose-plugin)" >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo "Docker не запущен или нет прав: запустите через sudo или добавьте пользователя в группу docker" >&2; exit 1; }
if [ ${#PROFILES[@]} -eq 0 ]; then
  # The stand's stack: local models when the delivery has them, and the telephony.
  [ -f "$HERE/models.tar" ] && PROFILES+=(--profile ai)
  PROFILES+=(--profile telephony)
fi
if [ -z "$HOST_ADDR" ]; then
  HOST_ADDR="$(hostname -I 2>/dev/null | awk '{print $1}')"
  HOST_ADDR="${HOST_ADDR:-localhost}"
fi

echo "== проверка поставки"
( cd "$HERE" && sha256sum -c SHA256SUMS )
cat "$HERE/VERSION"

echo "== образы"
docker load -i "$HERE/images.tar"

echo "== исходники → $TARGET"
mkdir -p "$TARGET"
tar -xf "$HERE/source.tar" -C "$TARGET"
if [ -f "$HERE/organizers.tar" ]; then
  tar -xf "$HERE/organizers.tar" -C "$TARGET"
fi
if [ -f "$HERE/models.tar" ]; then
  echo "== модели"
  tar -xf "$HERE/models.tar" -C "$TARGET"
fi
cd "$TARGET"
if [ ! -f .env ]; then
  cp .env.example .env
  secret="$(head -c 48 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  sed -i "s|^SECRET_KEY=.*|SECRET_KEY=${secret}|" .env
  sed -i "s|^TLS_HOSTS=.*|TLS_HOSTS=localhost,127.0.0.1,${HOST_ADDR}|" .env
  # The stand's settings (28.09.2026): production, the caller and the scenario generation on
  # Qwen2.5-3B with a 16k context, speech recognition on whisper small.
  sed -i "s|^APP_ENV=.*|APP_ENV=production|" .env
  sed -i "s|^LLM_DIALOG_MODEL=.*|LLM_DIALOG_MODEL=qwen2.5-3b-instruct-q4_k_m.gguf|" .env
  sed -i "s|^LLM_GEN_URL=.*|LLM_GEN_URL=http://llm-dialog:8080|" .env
  sed -i "s|^LLM_DIALOG_CTX=.*|LLM_DIALOG_CTX=16384|" .env
  sed -i "s|^STT_MODEL=.*|STT_MODEL=faster-whisper-small|" .env
  if [[ " ${PROFILES[*]} " == *" telephony "* ]]; then
    sed -i "s|^TELEPHONY_ENABLED=.*|TELEPHONY_ENABLED=true|" .env
    sed -i "s|^TELEPHONY_EXTERNAL_IP=.*|TELEPHONY_EXTERNAL_IP=${HOST_ADDR}|" .env
  fi
  echo "   создан .env: свой SECRET_KEY, адрес ${HOST_ADDR}; пароли — INSTALL.md, раздел 2"
fi

echo "== запуск (${PROFILES[*]:-без профилей})"
SCALE=()
# As on the stand: when the generation goes to the dialog model, the 7B server is not started.
if grep -q '^LLM_GEN_URL=http://llm-dialog:' .env && [[ " ${PROFILES[*]} " == *" ai "* ]]; then
  SCALE=(--scale llm-gen=0)
fi
docker compose "${PROFILES[@]}" up -d --no-build --pull never "${SCALE[@]}"

echo "== ожидание готовности"
for _ in $(seq 1 60); do
  if docker compose ps --format '{{.Name}} {{.Health}}' | grep -q 'backend-1 healthy'; then
    break
  fi
  sleep 5
done
docker compose ps --format 'table {{.Name}}\t{{.Status}}'
HTTPS_PORT=$(grep -E '^HTTPS_PORT=' .env | cut -d= -f2)
SEED=$(grep -E '^SEED_PASSWORD=' .env | cut -d= -f2)
PORT_SUFFIX=""
[ "${HTTPS_PORT:-443}" = "443" ] || PORT_SUFFIX=":${HTTPS_PORT}"
cat <<DONE

Готово. Откройте в браузере: https://${HOST_ADDR}${PORT_SUFFIX}
  преподаватель:  teacher / ${SEED}
  обучающийся:    student / ${SEED}
  администратор:  admin / ${SEED} (при первом входе попросит сменить пароль)
Браузер предупредит о сертификате, пока на рабочие места не установлен корневой сертификат:
  docker compose cp nginx:/etc/nginx/certs/ca.crt .   (INSTALL.md, раздел 2)
DONE
