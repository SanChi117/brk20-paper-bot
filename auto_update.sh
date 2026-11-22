#!/bin/bash
# BRK-20 автообновление: забирает main с GitHub, прогоняет тесты на новой версии
# и только при успехе переключается и перезапускает сервисы. Запускается таймером.
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR" || exit 1
LOG="$DIR/update.log"
note() { echo "$(date -u '+%Y-%m-%d %H:%M') UTC | $*" >> "$LOG"; }

git fetch -q origin main || { note "fetch не удался (нет сети?)"; exit 0; }
LOCAL=$(git rev-parse HEAD); REMOTE=$(git rev-parse origin/main)
[ "$LOCAL" = "$REMOTE" ] && exit 0

# проверяем новую версию в отдельной папке, рабочую не трогаем
TMP=$(mktemp -d)
git worktree add -q --detach "$TMP" origin/main
if (cd "$TMP" && python3 -m unittest discover -s tests > "$DIR/update_tests.log" 2>&1); then
  git worktree remove --force "$TMP"
  # правки, сделанные руками прямо на сервере, сохраняем в архив, а не теряем
  if ! git diff --quiet HEAD; then
    mkdir -p "$DIR/archive"
    P="$DIR/archive/local_changes_$(date -u +%Y%m%d_%H%M%S).patch"
    git diff HEAD > "$P"
    note "локальные правки на сервере сохранены в $P"
  fi
  git reset -q --hard origin/main
  for s in brk20 brk20s brk20-web; do
    systemctl is-enabled --quiet "$s" 2>/dev/null && systemctl restart "$s"
  done
  note "обновлено ${LOCAL:0:7} -> ${REMOTE:0:7}, сервисы перезапущены"
else
  git worktree remove --force "$TMP"
  note "тесты новой версии ${REMOTE:0:7} НЕ прошли — остаёмся на ${LOCAL:0:7} (см. update_tests.log)"
fi
