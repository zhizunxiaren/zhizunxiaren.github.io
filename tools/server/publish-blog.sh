#!/usr/bin/env bash
# 服务器端发布脚本：接收本机构建好的静态站点包，注入备案号后原子切换线上目录。
#
#   publish-blog.sh <site.tar.gz>     发布一个新版本
#   publish-blog.sh --rollback        回滚到上一个备份
#   publish-blog.sh --list            查看现有备份
#
# 由本机 `blog.cmd publish` 调用，也可在服务器上手动执行。
set -euo pipefail

LIVE_DIR=/mnt/data/hugo/live
STAGE_DIR=/mnt/data/hugo/staging
BACKUP_DIR=/mnt/data/hugo/releases
BEIAN_FILE=/etc/alphachowai/beian.html
KEEP_BACKUPS=5

log() { printf '[publish] %s\n' "$*"; }

inject_beian() {
  local dir="$1"
  if [ ! -f "$BEIAN_FILE" ]; then
    log "警告: 缺少 $BEIAN_FILE，跳过备案号注入"
    return 0
  fi
  local beian
  beian="$(cat "$BEIAN_FILE")"
  export BEIAN="$beian"
  local count
  count=$(grep -rl 'data-alphachowai-beian-placeholder' "$dir" --include='*.html' 2>/dev/null | wc -l || true)
  if [ "${count:-0}" -gt 0 ]; then
    grep -rl 'data-alphachowai-beian-placeholder' "$dir" --include='*.html' 2>/dev/null \
      | xargs -r perl -0pi -e 's#<span data-alphachowai-beian-placeholder></span>#$ENV{BEIAN}#g'
    log "备案号已注入 $count 个页面"
  else
    log "警告: 页面里没有备案号占位符"
  fi
}

backup_live() {
  if [ -d "$LIVE_DIR" ] && [ -n "$(ls -A "$LIVE_DIR" 2>/dev/null)" ]; then
    mkdir -p "$BACKUP_DIR"
    local ts
    ts=$(date +%Y%m%d-%H%M%S)
    tar czf "$BACKUP_DIR/live-$ts.tgz" -C "$(dirname "$LIVE_DIR")" "$(basename "$LIVE_DIR")"
    log "已备份当前版本: live-$ts.tgz"
  fi
  mkdir -p "$BACKUP_DIR"
  ls -1t "$BACKUP_DIR"/live-*.tgz 2>/dev/null | tail -n +$((KEEP_BACKUPS + 1)) | xargs -r rm -f
}

reload_web() {
  if systemctl is-active --quiet caddy; then
    systemctl reload caddy 2>/dev/null || systemctl restart caddy
    log "Caddy 已重载"
  else
    log "警告: Caddy 未在运行"
  fi
}

case "${1:-}" in
  --list)
    ls -lht "$BACKUP_DIR"/live-*.tgz 2>/dev/null || log "暂无备份"
    exit 0
    ;;
  --rollback)
    target="${2:-}"
    if [ -z "$target" ]; then
      target=$(ls -1t "$BACKUP_DIR"/live-*.tgz 2>/dev/null | head -1 || true)
    fi
    [ -n "$target" ] && [ -f "$target" ] || { log "找不到可回滚的备份"; exit 2; }
    rm -rf "$STAGE_DIR"; mkdir -p "$STAGE_DIR"
    tar xzf "$target" -C "$STAGE_DIR" --strip-components=1
    rsync -a --delete "$STAGE_DIR"/ "$LIVE_DIR"/
    reload_web
    log "已回滚到 $(basename "$target")"
    exit 0
    ;;
  ""|-h|--help)
    echo "usage: publish-blog.sh <site.tar.gz> | --rollback [backup.tgz] | --list"
    exit 2
    ;;
esac

TARBALL="$1"
[ -f "$TARBALL" ] || { log "文件不存在: $TARBALL"; exit 2; }

rm -rf "$STAGE_DIR"
mkdir -p "$STAGE_DIR"
tar xzf "$TARBALL" -C "$STAGE_DIR"
[ -f "$STAGE_DIR/index.html" ] || { log "包里没有 index.html，拒绝发布"; exit 3; }
log "已解包 $(du -sh "$STAGE_DIR" | cut -f1)"

inject_beian "$STAGE_DIR"
backup_live

mkdir -p "$LIVE_DIR"
rsync -a --delete "$STAGE_DIR"/ "$LIVE_DIR"/
chmod -R u+rwX,go+rX,go-w "$LIVE_DIR"

reload_web
rm -rf "$STAGE_DIR"
log "发布完成: $(date '+%Y-%m-%d %H:%M:%S')  页面数 $(find "$LIVE_DIR" -name '*.html' | wc -l)"
