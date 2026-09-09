#!/bin/sh
# A full, self-contained backup of the site and the documents behind it.
#
#   sh tools/backup.sh              -> ~/Backups/october7/<date>/
#   DEST=/Volumes/usb sh tools/backup.sh
#
# What it takes, and why each piece:
#
#   repo.bundle   every commit, branch and tag in one file. `git clone` it
#                 and you have the project back — history, workflow and all —
#                 with no GitHub account involved.
#   sources/      the PDFs and recordings the transcript was made from. These
#                 are the only things here that cannot be rebuilt: the site is
#                 derived from them, they are not derived from anything.
#   dist/         the built site, exactly as published, so it can be put on
#                 any static host within minutes of GitHub going away.
#   SHA256SUMS    so a restored copy can be checked rather than assumed.
#
# Nothing here needs a password or a network. That is the point: a backup
# that depends on an account is not a backup against losing the account.
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
DEST=${DEST:-"$HOME/Backups/october7"}
STAMP=$(date +%Y-%m-%d-%H%M)
OUT="$DEST/$STAMP"

mkdir -p "$OUT/sources"
cd "$ROOT"

git bundle create "$OUT/repo.bundle" --all >/dev/null 2>&1
git bundle verify "$OUT/repo.bundle" >/dev/null

# The source documents live outside the repo (they are large and some are
# purchased court files), so they are named here rather than globbed.
for f in \
  "$HOME/Downloads/DOC270425_27042025154550.pdf" \
  "$HOME/Downloads/verdict-of-supreme-court-from-21-may-25-in-case-54321-03-25-regarding-the-dismissal-of-the-head-of-shin-bet.pdf"
do
  [ -f "$f" ] && cp "$f" "$OUT/sources/" || echo "  missing, not backed up: $f" >&2
done

[ -d dist ] && cp -R dist "$OUT/dist"

( cd "$OUT" && find . -type f ! -name SHA256SUMS -exec shasum -a 256 {} \; \
  | sort -k2 > SHA256SUMS )

printf '%s\n' "backup: $OUT"
du -sh "$OUT" | awk '{print "  size:  " $1}'
awk 'END {print "  files: " NR}' "$OUT/SHA256SUMS"
printf '  restore: git clone %s/repo.bundle october7\n' "$OUT"
