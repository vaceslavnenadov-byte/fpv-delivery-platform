#!/bin/bash
# Сборка пакетов Debian 13: fpv-platform (amd64, сервер), fpv-drone-agent (all)
# и fpv-desktop (all, ярлыки клиентов для рабочих мест).
#
#   packaging/debian/build-debs.sh [--wheels DIR] [--out DIR] [--revision N] [--build-frontend]
#
# --wheels  каталог wheel-файлов для Python 3.13 / x86_64 (по умолчанию
#           ../fpv-delivery-packages/python/wheels, формируется scripts/fetch-wheels.sh)
# Результат: <out>/fpv-platform_<версия>_amd64.deb, fpv-drone-agent_<версия>_all.deb,
#            fpv-desktop_<версия>_all.deb, SHA256SUMS
set -Eeuo pipefail
umask 022

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
WHEELS="$(cd "$REPO/.." && pwd)/fpv-delivery-packages/python/wheels"
OUT="$REPO/build/debs"
REVISION=1
BUILD_FE=0

die() { echo "build-debs: ОШИБКА: $*" >&2; exit 1; }
while [[ $# -gt 0 ]]; do
  case "$1" in
    --wheels) WHEELS="${2:-}"; shift 2 ;;
    --out) OUT="${2:-}"; shift 2 ;;
    --revision) REVISION="${2:-}"; shift 2 ;;
    --build-frontend) BUILD_FE=1; shift ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) die "неизвестный параметр: $1" ;;
  esac
done
[[ "$REVISION" =~ ^[0-9]+$ ]] || die "--revision должно быть числом"
for c in dpkg-deb python3 gzip md5sum; do command -v "$c" >/dev/null || die "нет команды $c (пакет dpkg-dev)"; done
[[ -d "$WHEELS" ]] || die "нет каталога wheels: $WHEELS (fpv-delivery-packages/scripts/fetch-wheels.sh)"

UPSTREAM="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$REPO/backend/fpv/__init__.py")"
VERSION="$UPSTREAM-$REVISION"
if (( BUILD_FE )); then (cd "$REPO/frontend" && npm ci --no-audit --no-fund && npm run build); fi
[[ -f "$REPO/frontend/dist/index.html" ]] || die "frontend не собран (frontend/dist); используйте --build-frontend"

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$OUT"

finish_package() {  # finish_package <корень> <каталог шаблона> <архитектура> <имя>
  local root="$1" tpl="$2" arch="$3" name="$4"
  find "$root" -type d -name '__pycache__' -prune -exec rm -rf {} +
  find "$root" -type f -name '*.pyc' -delete
  mkdir -p "$root/DEBIAN" "$root/usr/share/doc/$name"
  printf '%s (%s) trixie; urgency=medium\n\n  * Сборка из %s.\n\n -- FPV Platform Team <fpv-platform@fpv.local>  %s\n' \
    "$name" "$VERSION" "$(git -C "$REPO" describe --always --dirty 2>/dev/null || echo source)" "$(date -R)" \
    | gzip -9n > "$root/usr/share/doc/$name/changelog.Debian.gz"
  cp "$tpl"/DEBIAN/{postinst,prerm,postrm} "$root/DEBIAN/"
  [[ -f "$tpl/DEBIAN/conffiles" ]] && cp "$tpl/DEBIAN/conffiles" "$root/DEBIAN/"
  # права: каталоги 0755, файлы 0644, исполняемые — 0755
  find "$root" -type d -exec chmod 0755 {} +
  find "$root" -type f -exec chmod 0644 {} +
  chmod 0755 "$root"/DEBIAN/{postinst,prerm,postrm}
  [[ -d "$root/usr/bin" ]] && chmod 0755 "$root"/usr/bin/*
  local size
  size="$(du -sk --exclude=DEBIAN "$root" | cut -f1)"
  sed -e "s/@VERSION@/$VERSION/" -e "s/@SIZE@/$size/" "$tpl/DEBIAN/control.in" > "$root/DEBIAN/control"
  (cd "$root" && find . -path ./DEBIAN -prune -o -type f -print0 | sort -z | xargs -0 md5sum | sed 's# \./# #' > DEBIAN/md5sums)
  local deb="$OUT/${name}_${VERSION}_${arch}.deb"
  dpkg-deb --root-owner-group -Zxz --build "$root" "$deb" >/dev/null
  echo "собран: $deb ($(du -h "$deb" | cut -f1))"
}

# ---------------------------------------------------------------- fpv-platform
P="$STAGE/fpv-platform"
cp -a "$HERE/fpv-platform/files" "$P"
mkdir -p "$P/usr/lib/fpv-platform/backend" "$P/usr/share/fpv-platform"
cp -a "$REPO/backend/fpv" "$P/usr/lib/fpv-platform/backend/"
cp "$REPO/backend/requirements.lock" "$REPO/backend/requirements-s3.lock" "$P/usr/lib/fpv-platform/backend/"
cp -a "$REPO/database" "$P/usr/lib/fpv-platform/"
cp -a "$REPO/frontend/dist" "$P/usr/share/fpv-platform/frontend"
cp "$REPO/.env.example" "$P/usr/share/doc/fpv-platform/env.example"
mkdir -p "$P/usr/share/fpv-platform/wheels"
# Только wheel-файлы, перечисленные в lock-файлах; отсутствие любого — ошибка сборки.
python3 - "$WHEELS" "$P/usr/share/fpv-platform/wheels" \
    "$REPO/backend/requirements.lock" "$REPO/backend/requirements-s3.lock" <<'PY'
import re, shutil, sys
from pathlib import Path
src, dst, *locks = sys.argv[1:]
norm = lambda s: re.sub(r"[-_.]+", "_", s).lower()
wheels = {}
for w in Path(src).glob("*.whl"):
    name, version = w.name.split("-")[:2]
    wheels.setdefault((norm(name), version), []).append(w)
missing = []
for lock in locks:
    for line in Path(lock).read_text().splitlines():
        m = re.match(r"^([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?==([^\s;]+)", line)
        if not m:
            continue
        key = (norm(m.group(1)), m.group(2))
        if key not in wheels:
            missing.append("%s==%s" % key)
            continue
        for w in wheels[key]:
            shutil.copy2(w, dst)
if missing:
    sys.exit("нет wheel-файлов: " + ", ".join(sorted(set(missing))))
print("wheels:", len(list(Path(dst).glob("*.whl"))))
PY
finish_package "$P" "$HERE/fpv-platform" amd64 fpv-platform

# ------------------------------------------------------------- fpv-drone-agent
A="$STAGE/fpv-drone-agent"
cp -a "$HERE/fpv-drone-agent/files" "$A"
mkdir -p "$A/usr/lib/fpv-drone-agent"
cp -a "$REPO/drone-agent/fpv_agent" "$A/usr/lib/fpv-drone-agent/"
cp "$REPO/drone-agent/README.md" "$A/usr/share/doc/fpv-drone-agent/README.md"
finish_package "$A" "$HERE/fpv-drone-agent" all fpv-drone-agent

# ----------------------------------------------------------------- fpv-desktop
D="$STAGE/fpv-desktop"
cp -a "$HERE/fpv-desktop/files" "$D"
finish_package "$D" "$HERE/fpv-desktop" all fpv-desktop

(cd "$OUT" && sha256sum ./*"_${VERSION}_"*.deb | sed 's# \./# #' > "SHA256SUMS-$VERSION")
echo "контрольные суммы: $OUT/SHA256SUMS-$VERSION"
