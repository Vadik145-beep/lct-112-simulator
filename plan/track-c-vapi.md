# Трек C. Облачный голос заявителя через Vapi

**Ветка:** `track-c/vapi` от `wave-11/bugfix`. **PRD:** раздел 2 (внешние ИИ только вне
закрытого контура), 9.3, 9.5. **Стенд:** Beget 155.212.186.2 (профиль `cloud`).

## Цель

Демо-режим приёма вызова, в котором заявителя целиком играет облачная связка Vapi
(распознавание → сильная модель → голос ElevenLabs), а не локальный конвейер
whisper → Qwen → Piper. Задержка ответа заявителя меньше секунды, речь живая, модель
держит роль. Локальный режим и стенд экспертизы не меняются: облако включается только
переменными окружения и отдельным профилем compose.

## Как устроено

```
софтфон стажёра (WebRTC) ─┐
                          ├─ asterisk-cloud (мост) ─ SIP-транк ─ sip.vapi.ai (заявитель)
бэкенд (ARI, вебхуки) ────┘         │
       ▲                            └─ запись разговора в storage/recordings
       └── https://<PUBLIC_HOST>/api/cloud/vapi/webhook ◄── Vapi (assistant-request,
                                                             transcript, status-update)
```

1. `attempt.issued` → бэкенд, как и раньше, звонит стажёру (`Local/s@trainer-out`).
2. Стажёр снял трубку → бэкенд создаёт мост, включает запись и набирает Vapi
   (`Local/s@vapi-out` → `PJSIP/<sip-user>@vapi`); номер вызывающего = одноразовый токен звонка.
3. Vapi принимает SIP-вызов и спрашивает у бэкенда `assistant-request`; бэкенд по токену
   находит попытку и отдаёт «временного ассистента»: промпт заявителя из сценария (персона,
   поведение, факты), первую реплику (`caller.opening`), голос, распознавание на русском.
4. Реплики приходят вебхуком `transcript` (final) и складываются в `attempts.dialog` как
   обычные ходы; панель и мониторинг видят их через `dialog.turn`.
5. Конец: стажёр положил трубку (`ChannelDestroyed`) или Vapi завершил вызов
   (`status-update: ended`) → `call.ended`, оценка по существующему движку.

Отдельный Asterisk (`deploy/asterisk-cloud/`): те же шаблоны WebRTC-эндпоинтов, плюс транк
на Vapi и диалплан `vapi-out`. В compose он под профилем `cloud` с сетевым псевдонимом
`asterisk`, поэтому nginx (`/ws/sip`) и бэкенд (`ARI_URL`) не меняются. Профили
`telephony` и `cloud` в одном проекте вместе не поднимать.

Вебхуки Vapi требуют HTTPS с настоящим сертификатом: nginx получает второй server-блок
для `PUBLIC_HOST` (Let's Encrypt через webroot), самоподписанный сертификат на IP остаётся
для локальной установки.

## Задачи

- [x] Настройки `CLOUD_VOICE_*`, `VAPI_*`; запрет при `ALLOW_EXTERNAL_AI=false`.
- [x] Клиент Vapi: SIP-номер (`provider=vapi`) заводится при старте, если его нет.
- [x] `CloudCallManager`: мост стажёр ↔ Vapi, без ExternalMedia и локального конвейера.
- [x] Вебхук `/api/cloud/vapi/webhook` с проверкой секрета; `assistant-request`,
      `transcript`, `status-update`, `end-of-call-report`.
- [x] Ходы диалога из транскрипта (`dialog.external_turn`), темы по ключевым словам.
- [x] `deploy/asterisk-cloud/`, сервис `asterisk-cloud`, nginx с Let's Encrypt.
- [x] Тесты: менеджер на стенде-заглушке ARI, вебхук, промпт, клиент Vapi.
- [ ] Стенд: сертификат, порты ufw, живой звонок из браузера.
- [ ] (P2) Набор Vapi заранее, пока софтфон звонит, чтобы заявитель заговорил сразу.
- [ ] (P2) Выбор голоса по `caller.voice` сценария.

## Проверка

```bash
cd backend && uv run pytest tests/telephony/test_cloud.py tests/api/test_cloud_webhook.py
```

На стенде: `docker compose --profile ai --profile cloud up -d`, занятие «Приём вызова»,
стажёр отвечает в браузере, заявитель говорит первым, реплики видны в панели, после
«Завершить» разбор с транскриптом и записью.
