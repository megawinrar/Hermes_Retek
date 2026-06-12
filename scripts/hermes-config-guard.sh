#!/bin/bash
# Hermes Config Guard — автовосстановление Bothub конфига
# Запускать через cron каждые 5 минут или в systemd/docker healthcheck

CONFIG_PATH="/var/lib/docker/volumes/hermes-data/_data/config.yaml"
STATE_DB="/var/lib/docker/volumes/hermes-data/_data/state.db"

# Правильные значения
CORRECT_MODEL="deepseek-v4-flash"
CORRECT_URL="https://openai.bothub.chat/v1"
CORRECT_KEY="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpZCI6IjVjNWVkODBkLTU1NmYtNDE4ZC05ZDAzLWFkMTY2MWUzY2IzMCIsImlzRGV2ZWxvcGVyIjp0cnVlLCJpYXQiOjE3ODEyMDYyNzEsImV4cCI6MjA5Njc4MjI3MSwianRpIjoiNWFGX1pSeWJ2and4TFdsQyJ9._G0zZOzquB0dwVICEAlo__uGDkPh41okE3bNpeNlGPk"

# Проверяем config.yaml
if [ -f "$CONFIG_PATH" ]; then
    CURRENT_MODEL=$(grep "default:" "$CONFIG_PATH" | head -1 | sed "s/.*default: //")

    if echo "$CURRENT_MODEL" | grep -q "yandex"; then
        echo "[$(date)] Yandex detected in config.yaml — fixing..."
        sed -i "s|default: yandexgpt/latest|default: $CORRECT_MODEL|" "$CONFIG_PATH"
        sed -i "s|base_url: http://hermes-yandex-proxy:8000/v1|base_url: $CORRECT_URL|" "$CONFIG_PATH"
        sed -i "s|api_key: sk-hermes-retek-proxy-2026|api_key: $CORRECT_KEY|" "$CONFIG_PATH"
        echo "[$(date)] config.yaml fixed"
        docker restart hermes-agent
        echo "[$(date)] hermes-agent restarted"
    fi
fi

# Проверяем state.db
if [ -f "$STATE_DB" ]; then
    YANDEX_COUNT=$(sqlite3 "$STATE_DB" "SELECT COUNT(*) FROM sessions WHERE model LIKE '%yandex%' OR billing_base_url LIKE '%yandex-proxy%';" 2>/dev/null)
    if [ "$YANDEX_COUNT" -gt 0 ] 2>/dev/null; then
        echo "[$(date)] Yandex in state.db — fixing..."
        sqlite3 "$STATE_DB" "UPDATE sessions SET model='$CORRECT_MODEL', billing_base_url='$CORRECT_URL', billing_provider='custom' WHERE model LIKE '%yandex%' OR billing_base_url LIKE '%yandex-proxy%';" 2>/dev/null
        echo "[$(date)] state.db fixed"
    fi
fi

# Тест API
API_TEST=$(curl -s -X POST "https://openai.bothub.chat/v1/chat/completions" \
    -H "Authorization: Bearer $CORRECT_KEY" \
    -H "Content-Type: application/json" \
    -d '{"model": "deepseek-v4-flash", "messages": [{"role": "user", "content": "ping"}], "max_tokens": 3}' 2>&1)

if echo "$API_TEST" | grep -q "gen-"; then
    echo "[$(date)] Bothub API OK"
else
    echo "[$(date)] Bothub API FAIL"
fi
