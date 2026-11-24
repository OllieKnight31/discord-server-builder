#!/bin/bash
# Installs the discord-server-builder skill for Claude Code.
#   - copies skill/ to ~/.claude/skills/discord-server-builder
#   - creates config.env from config.example.env, unless one is already there
#   - never overwrites or prints an existing config.env
#   - runs the offline tests (no network, no Discord, no real token)
set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$HERE/skill"
DEST="$HOME/.claude/skills/discord-server-builder"
CONFIG="$DEST/config.env"

if [ ! -f "$SRC/SKILL.md" ] || [ ! -f "$SRC/scripts/discord_build.py" ]; then
  echo "ERROR: cannot find the skill folder next to this script ($SRC)."
  echo "Run install.sh from inside the unzipped discord-server-builder folder."
  exit 1
fi
if [ "$SRC" = "$DEST" ]; then
  echo "ERROR: this folder is already the installed copy. Nothing to do."
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1 || ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1; then
  echo "ERROR: Python 3.8 or newer is needed and was not found."
  echo "On a Mac, open Terminal and run:  xcode-select --install"
  echo "Then run this installer again."
  exit 1
fi

mkdir -p "$DEST"
# Copy everything except any config.env and Python cache folders.
(cd "$SRC" && tar cf - --exclude='config.env' --exclude='__pycache__' --exclude='.DS_Store' .) | (cd "$DEST" && tar xf -)
echo "Installed the skill to: $DEST"

if [ -e "$CONFIG" ]; then
  echo "Kept your existing settings file (not changed): $CONFIG"
else
  cp "$DEST/config.example.env" "$CONFIG"
  echo "Created your settings file: $CONFIG"
fi
chmod 600 "$CONFIG"

echo
echo "Running the offline tests (nothing is sent to Discord)..."
if RESULT="$(PYTHONDONTWRITEBYTECODE=1 python3 "$DEST/tests/run_offline_tests.py" 2>&1)"; then
  echo "$RESULT" | tail -n 1
else
  echo "$RESULT" | grep -A3 '^FAIL' || true
  echo "$RESULT" | tail -n 1
  echo "WARNING: the offline tests did not all pass. Do not build on a real server until they do."
  exit 1
fi

cat <<EOF

Next steps:
  1. Create your Discord bot and copy its token (README.md, "Create the bot").
  2. Open the settings file and fill in the two values:
         open -e "$CONFIG"
  3. Invite the bot to your server (README.md, "Invite the bot").
  4. Start Claude Code and say:  set up my Discord server
EOF
