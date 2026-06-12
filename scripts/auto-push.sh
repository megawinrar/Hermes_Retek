#!/bin/bash
# Автосинхронизация конфигов Hermes на GitLab
# Запускается после изменений навыков, памяти, конфигов
# Создаёт новую версионную ветку для отката

set -e
DATE=$(date +%Y-%m-%d)
REPO="git@gitlab.com:retek2/hermes_retek.git"
export GIT_SSH_COMMAND="ssh -i ~/.ssh/id_ed25519 -o StrictHostKeyChecking=accept-new"

cd /tmp
rm -rf hermes-auto-sync
git clone --branch hermes-config --single-branch "$REPO" hermes-auto-sync
cd hermes-auto-sync

cp -r /opt/data/skills skills/
cp /opt/data/AGENTS.md AGENTS.md
cp /opt/data/SOUL.md SOUL.md 2>/dev/null || true
cp -r /opt/data/memories memories/ 2>/dev/null || true

git add -A
git commit -m "AUTOSYNC: $DATE" || true

# Обновляем текущую ветку
git push origin hermes-config

# Создаём версионную ветку
git checkout -b "hermes-config-$DATE"
git push origin "hermes-config-$DATE"

cd /
rm -rf hermes-auto-sync
echo "✅ Auto-sync complete: $DATE"
