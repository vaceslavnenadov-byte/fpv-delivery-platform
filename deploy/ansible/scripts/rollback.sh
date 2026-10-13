#!/bin/bash
# Безопасный откат поддерживаемых изменений на текущем узле.
#   application — переключение на предыдущий (или указанный) релиз приложения, данные не затрагиваются;
#   config      — восстановление последних резервных копий конфигурации, созданных Ansible (backup: true).
# Удаление данных, схемы БД, пользователей и форматирование дисков откатом не выполняются.
FPV_SCRIPT=rollback
source "$(dirname "$0")/lib.sh"
trap 'on_error $LINENO' ERR

INSTALL_ROOT="${FPV_INSTALL_ROOT:-/opt/fpv}"
CONFIG_FILES=(/etc/sudoers.d/60-fpv /etc/ssh/sshd_config.d/60-fpv.conf /etc/samba/smb.conf /etc/sssd/sssd.conf
              /etc/krb5.conf /etc/security/pam_mount.conf.xml /etc/apt/sources.list.d/debian.sources)

usage() {
  cat <<USAGE
Использование: $0 --component application|config [--to previous|<release-id>] [--list] [--confirm] [--no-restart]
  --list       показать доступные релизы / резервные копии и выйти
  --confirm    обязательное подтверждение изменения (без него выполняется только проверка)
USAGE
}
COMPONENT="" TARGET="previous" LIST=0 CONFIRM=0 RESTART=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --component) COMPONENT="${2:-}"; shift 2 ;;
    --to) TARGET="${2:-}"; shift 2 ;;
    --list) LIST=1; shift ;;
    --confirm) CONFIRM=1; shift ;;
    --no-restart) RESTART=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "$E_USAGE" "неизвестный параметр: $1" ;;
  esac
done
in_list "$COMPONENT" "application config" || { usage >&2; die "$E_USAGE" "укажите --component application|config (другие изменения откатом не поддерживаются)"; }
[[ "$TARGET" =~ ^[A-Za-z0-9._-]+$ ]] || die "$E_USAGE" "недопустимое значение --to"

if [[ "$COMPONENT" == "application" ]]; then
  [[ -d "$INSTALL_ROOT/releases" ]] || die "$E_ROLLBACK" "не найдено ни одного релиза в $INSTALL_ROOT/releases"
  current="$(readlink -f "$INSTALL_ROOT/current" 2>/dev/null || true)"
  if (( LIST )); then
    for d in "$INSTALL_ROOT"/releases/*/; do d="${d%/}"; [[ "$d" == "$current" ]] && echo "* $(basename "$d") (активный)" || echo "  $(basename "$d")"; done
    exit 0
  fi
  if [[ "$TARGET" == "previous" ]]; then
    [[ -f "$INSTALL_ROOT/previous-release" ]] || die "$E_ROLLBACK" "предыдущий релиз не зафиксирован"
    target="$(head -1 "$INSTALL_ROOT/previous-release")"
  else
    target="$INSTALL_ROOT/releases/$TARGET"
  fi
  [[ -x "$target/venv/bin/python" && -f "$target/backend/fpv/__init__.py" ]] || die "$E_ROLLBACK" "релиз $target неполный или не найден"
  [[ "$target" != "$current" ]] || die "$E_ROLLBACK" "релиз $target уже активен"
  log "откат приложения: $current → $target"
  log "ВНИМАНИЕ: миграции БД не откатываются; откат допустим, если схема совместима (см. docs/deployment.md)"
  if (( ! CONFIRM )); then log "проверка завершена; для выполнения добавьте --confirm"; exit 0; fi
  ln -sfn "$target" "$INSTALL_ROOT/current.new" && mv -Tf "$INSTALL_ROOT/current.new" "$INSTALL_ROOT/current"
  echo "$current" > "$INSTALL_ROOT/previous-release"
  logger -t fpv-rollback "приложение переключено на $target (было $current)" 2>/dev/null || true
  if (( RESTART )); then systemctl restart fpv-api fpv-worker; fi
  log "откат приложения выполнен"
else
  found=0
  for f in "${CONFIG_FILES[@]}"; do
    # shellcheck disable=SC2012  # имена резервных копий формирует Ansible, без пробелов
    latest="$(ls -1t "$f".*~ 2>/dev/null | head -1 || true)"
    [[ -n "$latest" ]] || continue
    found=1
    echo "$f ← $latest"
    if (( CONFIRM && ! LIST )); then cp -p "$latest" "$f"; log "восстановлен $f"; fi
  done
  (( found )) || die "$E_ROLLBACK" "резервные копии конфигурации не найдены"
  (( CONFIRM )) || log "проверка завершена; для восстановления добавьте --confirm, затем перезапустите затронутые службы"
fi
