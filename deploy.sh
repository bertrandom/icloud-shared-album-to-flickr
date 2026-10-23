#!/usr/bin/env bash
# Push the code to the server and install dependencies there.
#
# Anything in .gitignore (photos.db, logs/, .venv) is neither sent nor deleted,
# so the server keeps its own state. config/settings.json is ignored by git but
# sent anyway; the server uses its "prod" section.
#
# Usage: ./deploy.sh [-n]   (-n: dry run, show what would change)
set -euo pipefail

REMOTE_HOST=bertrand@server
REMOTE_DIR=/web/icloud-shared-album-to-flickr

cd "$(dirname "$0")"

dry_run=()
if [[ "${1:-}" == "-n" ]]; then
    dry_run=(--dry-run)
fi

rsync -avz --delete "${dry_run[@]}" \
    --exclude=.git \
    --include=/config/settings.json \
    --filter=':- .gitignore' \
    ./ "$REMOTE_HOST:$REMOTE_DIR/"

if [[ ${#dry_run[@]} -eq 0 ]]; then
    ssh "$REMOTE_HOST" "cd $REMOTE_DIR && ~/.local/bin/uv sync --frozen"
fi
