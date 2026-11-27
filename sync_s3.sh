#!/usr/bin/env bash
# Publish the StormScope Tucson site (web/) to an S3 website bucket.
#
#   bash sync_s3.sh all     # site files + all case studies + live runs
#   bash sync_s3.sh live    # only live runs (called by run_live.py after each run)
#   bash sync_s3.sh site    # only HTML/JS/CSS (after editing the viewer)
#   bash sync_s3.sh all --dryrun   # show what would change, upload nothing
#
# Settings come from s3.env (copy s3.env.example; s3.env is git-ignored).
set -euo pipefail
cd "$(dirname "$0")"

[ -f s3.env ] || { echo "s3.env not found: copy s3.env.example to s3.env and fill it in"; exit 1; }
# shellcheck disable=SC1091
source s3.env
: "${S3_BUCKET:?set S3_BUCKET in s3.env}"
PREFIX="${S3_PREFIX:-stormscope}"
DEST="s3://${S3_BUCKET}/${PREFIX}"
MODE="${1:-all}"
DRY="${2:-}"
AWS=(aws)
[ -n "${AWS_PROFILE:-}" ] && AWS+=(--profile "$AWS_PROFILE")
[ -n "${AWS_REGION:-}" ] && AWS+=(--region "$AWS_REGION")
SYNC=("${AWS[@]}" s3 sync --only-show-errors --no-progress $DRY)

NOCACHE="no-cache, max-age=0, must-revalidate"   # JSON indexes: always fresh
SHORT="public, max-age=300"                      # HTML/JS/CSS: 5 minutes
IMG_CASE="public, max-age=86400"                 # case-study images: 1 day
IMG_LIVE="public, max-age=31536000, immutable"   # live images: folders are unique per run

site() {
  echo "-> site files"
  "${SYNC[@]}" web/ "$DEST/" --exclude "data/*" --cache-control "$SHORT" --delete
}

cases() {
  echo "-> case-study images"
  "${SYNC[@]}" web/data/ "$DEST/data/" --exclude "*" --include "img/*" --cache-control "$IMG_CASE"
  echo "-> case-study data"
  "${SYNC[@]}" web/data/ "$DEST/data/" --exclude "*" --include "*.json" --exclude "live/*" \
    --content-type "application/json" --cache-control "$NOCACHE"
}

live() {
  [ -d web/data/live ] || { echo "-> no live runs yet"; return; }
  echo "-> live images"
  "${SYNC[@]}" web/data/live/ "$DEST/data/live/" --exclude "*" --include "*.png" --cache-control "$IMG_LIVE"
  echo "-> live data (after images, so the index never points at missing files)"
  "${SYNC[@]}" web/data/live/ "$DEST/data/live/" --exclude "*" --include "*/run.json" \
    --content-type "application/json" --cache-control "$NOCACHE"
  "${AWS[@]}" s3 cp ${DRY:+--dryrun} --only-show-errors web/data/live/index.json "$DEST/data/live/index.json" \
    --content-type "application/json" --cache-control "$NOCACHE"
  echo "-> remove pruned live runs"
  "${SYNC[@]}" web/data/live/ "$DEST/data/live/" --delete --exclude "*" --include "*/*"
}

invalidate() {
  # Only needed when the site sits behind CloudFront and HTML/JS changed.
  if [ -n "${CLOUDFRONT_ID:-}" ] && [ -z "$DRY" ]; then
    echo "-> CloudFront invalidation"
    "${AWS[@]}" cloudfront create-invalidation --distribution-id "$CLOUDFRONT_ID" \
      --paths "/${PREFIX}/index.html" "/${PREFIX}/about.html" "/${PREFIX}/app.js" "/${PREFIX}/style.css" >/dev/null
  fi
}

case "$MODE" in
  all)  site; cases; live; invalidate ;;
  live) live ;;
  site) site; invalidate ;;
  *) echo "usage: bash sync_s3.sh [all|live|site] [--dryrun]"; exit 1 ;;
esac
echo "done: ${SITE_URL:-$DEST}"
