#!/bin/bash
API_KEY="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpZCI6IjVjNWVkODBkLTU1NmYtNDE4ZC05ZDAzLWFkMTY2MWUzY2IzMCIsImlzRGV2ZWxvcGVyIjp0cnVlLCJpYXQiOjE3ODEyMDYyNzEsImV4cCI6MjA5Njc4MjI3MSwianRpIjoiNWFGX1pSeWJ2and4TFdsQyJ9._G0zZOzquB0dwVICEAlo__uGDkPh41okE3bNpeNlGPk"

RESPONSE=$(curl -s -X POST "https://openai.bothub.chat/v1/chat/completions" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"model": "deepseek-v4-flash", "messages": [{"role": "user", "content": "ping"}], "max_tokens": 3}' 2>&1)

if echo "${RESPONSE}" | grep -q "gen-"; then
    echo "Hermes API OK | Bothub DeepSeek V4 Flash | $(date '+%H:%M %Z')"
    echo 'Cost: $0.10/1M in | $0.20/1M out | Context: 1M tokens'
else
    echo "Hermes API FAIL | $(date '+%H:%M %Z') | ${RESPONSE:0:80}"
fi
