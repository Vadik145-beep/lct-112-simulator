#!/usr/bin/env bash
# Одна команда для сервера с интернетом: скачивает модели, собирает и запускает тренажёр так же,
# как на стенде (README). Для сервера без интернета — полная поставка (docs/INSTALL.md, раздел 6).
#
#   ./start.sh [--host АДРЕС]
#
# Модели (~9 ГБ) берутся с Google Диска одним архивом; если он недоступен — по файлу с
# официальных источников (scripts/fetch_models.sh). Повторный запуск ничего не скачивает заново
# и не трогает уже созданный .env.
set -euo pipefail
cd "$(dirname "$0")"

# Архив моделей на Google Диске (models.tar из scripts/offline_bundle.sh --with-models).
MODELS_URL="${MODELS_URL:-https://drive.usercontent.google.com/download?id=1Cg23w3AxHc34QQo-TWKnoTCS_vsJVe3X&export=download&confirm=t}"
MODELS_SHA256="82e7de18d037490524e3b2ae9f4d5cb5ff6bec7e756da514246276119ceb501d"
# The file that tells the models are in place: the caller's model of the stand.
MODELS_MARK="models/llm/qwen2.5-3b-instruct-q4_k_m.gguf"

HOST_ADDR=""
while [ $# -gt 0 ]; do
  case "$1" in
    --host) HOST_ADDR="$2"; shift ;;
    *) echo "неизвестный параметр: $1" >&2; exit 2 ;;
  esac
  shift
done

command -v docker >/dev/null || {
  echo "Docker не найден. Установите Docker Engine и docker compose v2: https://docs.docker.com/engine/install/" >&2
  exit 1
}
docker compose version >/dev/null 2>&1 || { echo "нужен docker compose v2 (пакет docker-compose-plugin)" >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo "Docker не запущен или нет прав: запустите через sudo или добавьте пользователя в группу docker" >&2; exit 1; }
if [ -z "$HOST_ADDR" ]; then
  HOST_ADDR="$(hostname -I 2>/dev/null | awk '{print $1}')"
  HOST_ADDR="${HOST_ADDR:-localhost}"
fi

echo "== модели"
if [ -f "$MODELS_MARK" ]; then
  echo "   уже на месте"
else
  got=0
  if [ -n "$MODELS_URL" ]; then
    echo "   архив с Google Диска (~9 ГБ); после обрыва докачивается с того же места"
    # -C -: a broken download continues where it stopped, on a retry and on the next ./start.sh.
    if curl -L --fail --retry 20 --retry-delay 5 -C - --progress-bar -o models.tar.part "$MODELS_URL"; then
      if [ "$(sha256sum models.tar.part | cut -d' ' -f1)" = "$MODELS_SHA256" ]; then
        tar -xf models.tar.part && rm -f models.tar.part && got=1
      else
        echo "   архив повреждён — удаляю и качаю по файлу с официальных источников"
        rm -f models.tar.part
      fi
    else
      echo "   архив не докачался (запустите ./start.sh ещё раз — продолжит с места обрыва);"
      echo "   пока качаю по файлу с официальных источников"
    fi
  fi
  if [ "$got" != 1 ]; then
    ./scripts/fetch_models.sh && rm -f models.tar.part
  fi
fi

if [ ! -f .env ]; then
  echo "== настройки (.env) как на стенде"
  cp .env.example .env
  secret="$(head -c 48 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  sed -i "s|^SECRET_KEY=.*|SECRET_KEY=${secret}|" .env
  sed -i "s|^TLS_HOSTS=.*|TLS_HOSTS=localhost,127.0.0.1,${HOST_ADDR}|" .env
  # The stand's settings (28.09.2026): production, the caller and the scenario generation on
  # Qwen2.5-3B with a 16k context, speech recognition on whisper small, the telephony on.
  sed -i "s|^APP_ENV=.*|APP_ENV=production|" .env
  sed -i "s|^LLM_DIALOG_MODEL=.*|LLM_DIALOG_MODEL=qwen2.5-3b-instruct-q4_k_m.gguf|" .env
  sed -i "s|^LLM_GEN_URL=.*|LLM_GEN_URL=http://llm-dialog:8080|" .env
  sed -i "s|^LLM_DIALOG_CTX=.*|LLM_DIALOG_CTX=16384|" .env
  sed -i "s|^STT_MODEL=.*|STT_MODEL=faster-whisper-small|" .env
  sed -i "s|^TELEPHONY_ENABLED=.*|TELEPHONY_ENABLED=true|" .env
  sed -i "s|^TELEPHONY_EXTERNAL_IP=.*|TELEPHONY_EXTERNAL_IP=${HOST_ADDR}|" .env
fi

echo "== сборка и запуск (10–30 минут при первом запуске)"
# As on the stand: the generation goes to the dialog model, the 7B server is not started.
docker compose --profile ai --profile telephony up -d --build --scale llm-gen=0

echo "== ожидание готовности"
for _ in $(seq 1 90); do
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
  docker compose cp nginx:/etc/nginx/certs/ca.crt .   (docs/INSTALL.md, раздел 2)
Режим «с интернетом» (Vapi) включается своими ключами: docs/INSTALL.md.
DONE
