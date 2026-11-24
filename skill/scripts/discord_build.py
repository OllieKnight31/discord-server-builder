#!/usr/bin/env python3
"""
discord_build.py: build a Discord server layout from one JSON file.

Creates roles, categories and channels, sets who can see and post where, deals
with the channels that were already there (keep, archive or replace), points a
Community server at its new rules and announcements channels, then puts
everything in order. Needs Python 3.8 or newer and nothing else.

USAGE:
  python3 discord_build.py --config layout.json --dry-run     # preview, changes nothing
  python3 discord_build.py --config layout.json --verify      # build, then check the result
  python3 discord_build.py --config layout.json --check       # check only, changes nothing
  python3 discord_build.py --verify                           # print the current structure

  Add --guild SERVER_ID to pick a server other than the one in your settings.
  Add --existing keep|archive|replace to override the layout file.

SETTINGS (bot token and server id):
  Read from environment variables first, then from the settings file
  config.env in the skill folder (or the file named by --env-file or by
  the DISCORD_BUILDER_CONFIG environment variable):
      DISCORD_BOT_TOKEN=...
      DISCORD_GUILD_ID=...

IMPORTANT:
  --dry-run is a preview, not an offline mode. It still reads the token and
  asks Discord what is in the server (read-only GET requests). It never sends
  a request that changes anything.

  "replace" deletes every existing channel and its message history. It will
  not run unless you add --confirm-replace followed by the server id.

EXIT CODES: 0 fine, 1 the build or check hit a problem, 2 setup problem
(nothing was changed).
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from permissions import PERMS, names_for  # noqa: E402

API = "https://discord.com/api/v10"
ADMIN = PERMS["ADMINISTRATOR"]
VIEW = PERMS["VIEW_CHANNEL"]
SEND = PERMS["SEND_MESSAGES"]
MANAGE_CHANNELS = PERMS["MANAGE_CHANNELS"]
MANAGE_ROLES = PERMS["MANAGE_ROLES"]
MANAGE_GUILD = PERMS["MANAGE_GUILD"]
TYPE = {"text": 0, "voice": 2, "news": 5, "stage": 13, "forum": 15}
COMMUNITY_ONLY = {"news", "stage", "forum"}
TEXT_LIKE = {0, 5, 15}
CATEGORY = 4
CATEGORY_LIMIT = 50
MODES = {"keep": "keep", "archive": "archive", "replace": "replace",
         "fresh": "replace", "start-fresh": "replace", "start fresh": "replace"}

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SETTINGS = os.path.join(SKILL_DIR, "config.env")
PLACEHOLDERS = {"", "PASTE_YOUR_BOT_TOKEN_HERE", "PASTE_YOUR_SERVER_ID_HERE"}


def stop(msg, code=2):
    """Stop with a plain message, no stack trace."""
    print("\nERROR: " + msg, file=sys.stderr)
    sys.exit(code)


# ---------------------------------------------------------------- settings

def read_settings(path):
    """Read KEY=VALUE lines. Ignores blank lines, comments, 'export ' and quotes."""
    out = {}
    if not path or not os.path.isfile(path):
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[len("export "):].strip()
            k, v = line.split("=", 1)
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            out[k.strip()] = v.strip()
    return out


def settings_path(cli_value):
    p = cli_value or os.environ.get("DISCORD_BUILDER_CONFIG") or DEFAULT_SETTINGS
    return os.path.abspath(os.path.expanduser(p))


def get_setting(key, path, settings):
    v = (os.environ.get(key) or "").strip()
    if v and v not in PLACEHOLDERS:
        return v
    v = (settings.get(key) or "").strip()
    if v and v not in PLACEHOLDERS:
        return v
    return None


def load_token(path, settings):
    tok = get_setting("DISCORD_BOT_TOKEN", path, settings)
    if not tok:
        exists = "it exists but the token line is empty or still the placeholder" \
            if os.path.isfile(path) else "that file does not exist yet"
        stop("No Discord bot token found.\n"
             "  Put it in the settings file:  %s\n"
             "      (%s)\n"
             "      as a line reading:  DISCORD_BOT_TOKEN=your-token-here\n"
             "  or set an environment variable:  export DISCORD_BOT_TOKEN=your-token-here\n"
             "  How to get a token: README.md, section \"Create the bot\"." % (path, exists))
    if tok.lower().startswith("bot "):
        tok = tok[4:].strip()
    if re.search(r"\s", tok):
        stop("The bot token contains a space or line break. Copy it again from the "
             "Developer Portal (Bot, Reset Token) and paste it on one line.")
    return tok


def load_guild(cli_guild, cfg, path, settings):
    cfg_guild = str(cfg.get("guild_id") or "").strip()
    if cli_guild and cfg_guild and cli_guild != cfg_guild:
        stop("Two different servers were named: --guild %s and guild_id %s in the layout file.\n"
             "  Nothing was changed. Remove one of them so there is no doubt which server is meant."
             % (cli_guild, cfg_guild))
    guild = cli_guild or cfg_guild or get_setting("DISCORD_GUILD_ID", path, settings)
    if not guild:
        stop("No server id found.\n"
             "  Pass it on the command line:  --guild 123456789012345678\n"
             "  or put it in the settings file %s as:  DISCORD_GUILD_ID=123456789012345678\n"
             "  or set an environment variable:  export DISCORD_GUILD_ID=123456789012345678\n"
             "  How to find it: README.md, section \"Find your server id\"." % path)
    if not re.fullmatch(r"\d{15,21}", guild):
        stop("The server id %r does not look right. It should be 17 to 20 digits and nothing else.\n"
             "  In Discord: Settings, Advanced, switch on Developer Mode, then right-click the\n"
             "  server icon and choose Copy Server ID." % guild)
    return guild


# ---------------------------------------------------------------- Discord API

class DiscordError(Exception):
    def __init__(self, method, path, status, code, message):
        Exception.__init__(self, "%s %s -> HTTP %s (code %s): %s" % (method, path, status, code, message))
        self.method, self.path, self.status, self.code, self.message = method, path, status, code, message


class Client:
    """Tiny Discord REST client. In dry-run mode it refuses to send anything but GET."""

    def __init__(self, token, dry=False):
        self.token = token
        self.dry = dry
        self.writes = 0

    def __call__(self, method, path, body=None):
        if method != "GET":
            if self.dry:
                shown = json.dumps(body, ensure_ascii=False) if body is not None else ""
                print(("    [dry-run] %s %s %s" % (method, path, shown))[:300])
                return {}
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(API + path, data=data, method=method)
        r.add_header("Authorization", "Bot " + self.token)
        r.add_header("Content-Type", "application/json")
        r.add_header("User-Agent", "DiscordServerBuilder (https://local, 1.0)")
        limited = 0
        server_errors = 0
        while True:
            try:
                with urllib.request.urlopen(r, timeout=30) as resp:
                    txt = resp.read().decode()
                if method != "GET":
                    self.writes += 1
                    time.sleep(0.4)      # stay well under Discord's write limits
                return json.loads(txt) if txt else {}
            except urllib.error.HTTPError as e:
                raw = e.read().decode(errors="replace")
                try:
                    parsed = json.loads(raw)
                except ValueError:
                    parsed = {}
                if not isinstance(parsed, dict):
                    parsed = {}
                if e.code == 429:
                    limited += 1
                    wait = parsed.get("retry_after")
                    if wait is None:
                        wait = (e.headers.get("Retry-After") if e.headers else None) or 1.0
                    try:
                        wait = float(wait)
                    except (TypeError, ValueError):
                        wait = 1.0
                    if limited > 8 or wait > 300:
                        raise DiscordError(method, path, 429, parsed.get("code"),
                                           "Discord is rate limiting this bot (asked for a %.0f second wait). "
                                           "Wait a few minutes and run the same command again." % wait)
                    print("    (rate limited by Discord, waiting %.1fs then retrying)" % (wait + 0.5))
                    time.sleep(wait + 0.5)
                    continue
                if e.code >= 500 and server_errors < 3:
                    server_errors += 1
                    time.sleep(2.0)
                    continue
                raise DiscordError(method, path, e.code, parsed.get("code"),
                                   parsed.get("message") or raw[:200] or "no details")
            except urllib.error.URLError as e:
                reason = str(getattr(e, "reason", e))
                if "CERTIFICATE_VERIFY_FAILED" in reason:
                    stop("Python could not verify Discord's security certificate.\n"
                         "  This happens with Python installed from python.org on a Mac. Fix: open\n"
                         "  Applications, the Python folder, and double-click \"Install Certificates.command\".\n"
                         "  Or run this with the Mac's own Python:  /usr/bin/python3 ...", 1)
                stop("Could not reach Discord (%s). Check the internet connection and try again.\n"
                     "  Anything already created stays; running the same command again carries on safely."
                     % reason, 1)


def explain(err, guild):
    """Plain-English reason for a Discord error."""
    if err.status == 401:
        return ("Discord rejected the bot token (401 Unauthorized). It is wrong, incomplete, or was reset.\n"
                "  Go to the Developer Portal, your application, Bot, Reset Token, and paste the new one\n"
                "  into the settings file.")
    if err.status == 403 and err.code == 50001 or err.status == 404 and err.code == 10004:
        return ("The bot cannot see server %s. Either the server id is wrong or the bot has not been\n"
                "  invited to that server yet. See README.md, section \"Invite the bot\"." % guild)
    if err.status == 403:
        return ("Discord refused: the bot is missing a permission (403, code %s: %s).\n"
                "  Usual causes: (1) the bot was invited without Administrator but the layout creates a\n"
                "  role that has it; (2) the bot's own role sits below a role it is trying to manage, so\n"
                "  drag the bot's role to the top in Server Settings, Roles; (3) the layout uses a\n"
                "  permission the bot does not hold. Re-invite it with the link from permissions.py."
                % (err.code, err.message))
    if err.code == 30013:
        return "The server has hit Discord's limit of 500 channels. Delete some channels first."
    if err.code == 30005:
        return "The server has hit Discord's limit of 250 roles. Delete some roles first."
    return str(err)


# ---------------------------------------------------------------- layout file

def load_config(path):
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    except OSError as e:
        stop("Cannot read the layout file %s (%s)." % (path, e.strerror or e))
    except ValueError as e:
        stop("The layout file %s is not valid JSON: %s" % (path, e))
    if not isinstance(cfg, dict):
        stop("The layout file %s must be a JSON object." % path)
    return cfg


def uncategorised(cfg):
    return cfg.get("uncategorised") or cfg.get("uncategorized") or []


def display_name(item, sep):
    emoji = item.get("emoji")
    return "%s%s%s" % (emoji, sep, item["name"]) if emoji else item["name"]


def validate_config(cfg, mode_override):
    """Return (mode, warnings). Stops with a clear message on anything that would break the build."""
    errors, warnings = [], []
    opts = cfg.get("options") or {}
    raw_mode = mode_override or opts.get("existing_channels", "keep")
    mode = MODES.get(str(raw_mode).strip().lower())
    if not mode:
        errors.append("options.existing_channels is %r; it must be keep, archive or replace." % raw_mode)
    sep = opts.get("separator", "・")

    seen_roles = set()
    for r in cfg.get("roles", []):
        if not isinstance(r, dict) or not str(r.get("name") or "").strip():
            errors.append("A role has no name.")
            continue
        if r["name"] in seen_roles:
            errors.append("Role %r is listed twice." % r["name"])
        seen_roles.add(r["name"])
        try:
            int(str(r.get("permissions", "0")))
        except ValueError:
            errors.append("Role %r: permissions must be a number in quotes, for example \"0\"." % r["name"])
        col = r.get("colour", r.get("color"))
        if isinstance(col, str) and not re.fullmatch(r"#?[0-9A-Fa-f]{6}", col):
            errors.append("Role %r: colour must be six hex digits such as F1C40F." % r["name"])

    names = {}

    def check_channel(ch, where):
        if not isinstance(ch, dict) or not str(ch.get("name") or "").strip():
            errors.append("A channel in %s has no name." % where)
            return
        t = ch.get("type", "text")
        if t not in TYPE:
            errors.append("Channel %r in %s: type %r is not one of %s."
                          % (ch["name"], where, t, ", ".join(sorted(TYPE))))
        elif t in COMMUNITY_ONLY:
            warnings.append("Channel %r is a %s channel, which Discord only allows on Community servers."
                            % (ch["name"], t))
        if len(display_name(ch, sep)) > 100:
            errors.append("Channel %r in %s: the name is longer than Discord's 100 characters." % (ch["name"], where))
        names.setdefault(ch["name"], []).append(where)

    for ch in uncategorised(cfg):
        check_channel(ch, "the uncategorised list")
    seen_cats = set()
    for cat in cfg.get("categories", []):
        if not isinstance(cat, dict) or not str(cat.get("name") or "").strip():
            errors.append("A category has no name.")
            continue
        if cat["name"] in seen_cats:
            errors.append("Category %r is listed twice." % cat["name"])
        seen_cats.add(cat["name"])
        if len(cat["name"]) > 100:
            errors.append("Category %r: the name is longer than Discord's 100 characters." % cat["name"])
        chans = cat.get("channels", [])
        if len(chans) > CATEGORY_LIMIT:
            errors.append("Category %r has %d channels; Discord allows %d per category."
                          % (cat["name"], len(chans), CATEGORY_LIMIT))
        local = set()
        for ch in chans:
            check_channel(ch, "category %r" % cat["name"])
            if isinstance(ch, dict) and ch.get("name") in local:
                errors.append("Channel %r appears twice in category %r." % (ch["name"], cat["name"]))
            if isinstance(ch, dict):
                local.add(ch.get("name"))

    comm = cfg.get("community") or {}
    for key in ("rules_channel", "updates_channel"):
        if comm.get(key) and comm[key] not in names:
            errors.append("community.%s points at %r, which is not a channel in this layout." % (key, comm[key]))
        elif comm.get(key) and len(names[comm[key]]) > 1:
            errors.append("community.%s points at %r, but that name is used more than once." % (key, comm[key]))

    if errors:
        stop("The layout file has %d problem(s). Nothing was changed.\n  - %s" % (len(errors), "\n  - ".join(errors)))
    return mode, warnings


def compute_overwrites(item, parent, role_map, everyone, staff_roles, bot_id=None, missing=None):
    """Permission overwrites from the private / readonly flags (private is inherited from the category)."""
    merged = {}

    def add(rid, kind=0, allow=0, deny=0):
        m = merged.setdefault(rid, {"type": kind, "allow": 0, "deny": 0})
        m["allow"] |= allow
        m["deny"] |= deny

    def roles_named(names, bit):
        for n in names:
            if n in role_map:
                add(role_map[n], allow=bit)
            elif missing is not None:
                missing.add(n)

    parent = parent or {}
    if item.get("private") or parent.get("private"):
        add(everyone, deny=VIEW)
        roles_named(item.get("allow_roles") or parent.get("allow_roles") or staff_roles, VIEW)
        if bot_id:
            add(bot_id, kind=1, allow=VIEW)      # so a non-admin bot can still manage what it hides
    if item.get("readonly"):
        add(everyone, deny=SEND)
        roles_named(item.get("post_roles") or staff_roles, SEND)
    return [{"id": k, "type": v["type"], "allow": str(v["allow"]), "deny": str(v["deny"])}
            for k, v in merged.items()]


def has_overwrites(cfg):
    for cat in cfg.get("categories", []):
        if cat.get("private"):
            return True
        for ch in cat.get("channels", []):
            if ch.get("private") or ch.get("readonly"):
                return True
    return any(ch.get("private") or ch.get("readonly") for ch in uncategorised(cfg))


def norm(name, ctype):
    """Discord lower-cases text channel names and turns spaces into hyphens."""
    return name.lower().replace(" ", "-") if ctype in TEXT_LIKE else name


# ---------------------------------------------------------------- reading the server

def print_structure(req, guild):
    cs = req("GET", "/guilds/%s/channels" % guild)
    g = req("GET", "/guilds/%s" % guild)
    t = {0: "#", 2: "🔊", 4: "CAT", 5: "📣", 13: "STAGE", 15: "FORUM"}
    cats = sorted([c for c in cs if c["type"] == CATEGORY], key=lambda c: c.get("position", 0))
    uncat = sorted([c for c in cs if not c.get("parent_id") and c["type"] != CATEGORY],
                   key=lambda c: c.get("position", 0))
    print("\n=== %s (%s) ===" % (g.get("name"), guild))
    for c in uncat:
        print("  %s %s  [%d ow]" % (t.get(c["type"], "?"), c["name"], len(c.get("permission_overwrites", []))))
    for cat in cats:
        print("\n📁 %s  [%d ow]" % (cat["name"], len(cat.get("permission_overwrites", []))))
        kids = [x for x in cs if x.get("parent_id") == cat["id"]]
        for k in sorted(kids, key=lambda x: (x["type"], x.get("position", 0))):
            print("   %s %s  [%d ow]" % (t.get(k["type"], "?"), k["name"], len(k.get("permission_overwrites", []))))
    print("\nrules_channel_id: %s  public_updates_channel_id: %s"
          % (g.get("rules_channel_id"), g.get("public_updates_channel_id")))
    print("features: %s  total channels: %d" % (g.get("features"), len(cs)))
    print("([N ow] = number of permission overwrites on that channel)")


def is_archive_name(name, base):
    return name == base or re.fullmatch(re.escape(base) + r" \d+", name) is not None


def check(req, guild, cfg, mode):
    """Compare the live server with the layout file. Read-only. Returns the number of failures."""
    opts = cfg.get("options") or {}
    sep = opts.get("separator", "・")
    arch_base = opts.get("archive_category_name", "📦 Archive")
    cs = req("GET", "/guilds/%s/channels" % guild)
    roles = req("GET", "/guilds/%s/roles" % guild)
    g = req("GET", "/guilds/%s" % guild)
    passed, failed = [], []

    def deny_bits(ch):
        for ow in ch.get("permission_overwrites", []):
            if str(ow.get("id")) == str(guild):
                return int(ow.get("deny", 0))
        return 0

    def flags(item, parent, live, label):
        if item.get("private") or (parent or {}).get("private"):
            (passed if deny_bits(live) & VIEW else failed).append("%s is hidden from @everyone" % label)
        if item.get("readonly"):
            (passed if deny_bits(live) & SEND else failed).append("%s is read-only for @everyone" % label)

    role_names = {r["name"] for r in roles}
    for r in cfg.get("roles", []):
        (passed if r["name"] in role_names else failed).append("role %s exists" % r["name"])

    wanted_ids = set()
    found = {}

    def one_channel(ch, parent_cfg, parent_live):
        ctype = TYPE[ch.get("type", "text")]
        nm = display_name(ch, sep)
        pid = parent_live["id"] if parent_live else None
        live = next((c for c in cs if c["type"] == ctype and (c.get("parent_id") or None) == pid
                     and norm(c["name"], ctype) == norm(nm, ctype)), None)
        where = "in %s" % parent_cfg["name"] if parent_cfg else "at the top"
        if not live:
            failed.append("channel %s exists %s" % (nm, where))
            return
        passed.append("channel %s exists %s" % (nm, where))
        wanted_ids.add(live["id"])
        found[ch["name"]] = live["id"]
        flags(ch, parent_cfg, live, "channel %s" % nm)

    for ch in uncategorised(cfg):
        one_channel(ch, None, None)
    for cat in cfg.get("categories", []):
        live_cat = next((c for c in cs if c["type"] == CATEGORY and c["name"] == cat["name"]), None)
        if not live_cat:
            failed.append("category %s exists" % cat["name"])
            for ch in cat.get("channels", []):
                failed.append("channel %s exists in %s" % (display_name(ch, sep), cat["name"]))
            continue
        passed.append("category %s exists" % cat["name"])
        wanted_ids.add(live_cat["id"])
        flags(cat, None, live_cat, "category %s" % cat["name"])
        for ch in cat.get("channels", []):
            one_channel(ch, cat, live_cat)

    comm = cfg.get("community") or {}
    if comm and "COMMUNITY" in (g.get("features") or []):
        for key, field in (("rules_channel", "rules_channel_id"), ("updates_channel", "public_updates_channel_id")):
            if comm.get(key):
                ok = found.get(comm[key]) and str(g.get(field)) == str(found[comm[key]])
                (passed if ok else failed).append("Community %s points at %s" % (field, comm[key]))

    arch_ids = {c["id"] for c in cs if c["type"] == CATEGORY and is_archive_name(c["name"], arch_base)}
    others = [c for c in cs if c["id"] not in wanted_ids and c["id"] not in arch_ids]
    if mode == "archive":
        loose = [c for c in others if c["type"] == CATEGORY or c.get("parent_id") not in arch_ids]
        if loose:
            failed.append("every old channel is in the archive (still outside: %s)"
                          % ", ".join(c["name"] for c in loose[:8]))
        else:
            passed.append("every old channel is in the archive")
        for a in [c for c in cs if c["id"] in arch_ids]:
            (passed if deny_bits(a) & VIEW else failed).append("archive category %s is hidden from @everyone" % a["name"])
    elif mode == "replace":
        left = others + [c for c in cs if c["id"] in arch_ids]
        if left:
            failed.append("no old channels remain (still there: %s)" % ", ".join(c["name"] for c in left[:8]))
        else:
            passed.append("no old channels remain")

    print("\n=== CHECK against the layout (%s) ===" % cfg.get("name", "layout"))
    for p in passed:
        print("  PASS  " + p)
    for f in failed:
        print("  FAIL  " + f)
    print("CHECK RESULT: %d passed, %d failed" % (len(passed), len(failed)))
    return len(failed)


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(
        description="Build a Discord server layout from a JSON file. "
                    "--dry-run previews without changing anything (it still reads the server).")
    ap.add_argument("--config", help="layout JSON file (see scripts/templates/)")
    ap.add_argument("--guild", help="server id; overrides DISCORD_GUILD_ID from the settings")
    ap.add_argument("--env-file", "--env", dest="env_file",
                    help="settings file holding DISCORD_BOT_TOKEN and DISCORD_GUILD_ID "
                         "(default: config.env in the skill folder)")
    ap.add_argument("--existing", choices=["keep", "archive", "replace"],
                    help="what to do with channels already in the server; overrides the layout file")
    ap.add_argument("--confirm-replace", metavar="SERVER_ID",
                    help="required for replace: repeat the server id to confirm the deletions")
    ap.add_argument("--dry-run", action="store_true", help="preview only; sends no request that changes anything")
    ap.add_argument("--verify", action="store_true",
                    help="with --config: build, then print and check the result. Alone: print the structure")
    ap.add_argument("--check", action="store_true",
                    help="compare the server with --config and change nothing")
    a = ap.parse_args()

    if a.check and not a.config:
        stop("--check needs --config so it knows which layout to compare against.")
    if not a.config and not a.verify:
        stop("Nothing to do. Give --config layout.json to build, or --verify to print the current structure.\n"
             "  Run with --help for the full list.")

    cfg, mode, warnings = {}, "keep", []
    if a.config:
        cfg = load_config(a.config)
        mode, warnings = validate_config(cfg, a.existing)

    path = settings_path(a.env_file)
    settings = read_settings(path)
    token = load_token(path, settings)
    guild = load_guild(a.guild, cfg, path, settings)
    req = Client(token, dry=a.dry_run or a.check or not a.config)

    try:
        return run(a, cfg, mode, warnings, req, guild)
    except DiscordError as e:
        print("\nSTOPPED: " + explain(e, guild), file=sys.stderr)
        if req.writes:
            print("  %d change(s) had already been made. Nothing is lost: fix the cause and run the same\n"
                  "  command again. Anything already created is found and reused, not duplicated." % req.writes,
                  file=sys.stderr)
        else:
            print("  Nothing in the server was changed.", file=sys.stderr)
        sys.exit(1 if req.writes else 2)


def run(a, cfg, mode, warnings, req, guild):
    # --- who am I, and can I see the server? (read-only) ---
    me = req("GET", "/users/@me")
    g = req("GET", "/guilds/%s" % guild)
    gname = g.get("name") or "?"

    if not a.config:
        print_structure(req, guild)
        return
    if a.check:
        if check(req, guild, cfg, mode):
            sys.exit(1)
        return

    opts = cfg.get("options") or {}
    sep = opts.get("separator", "・")
    staff_roles = opts.get("staff_roles", [])
    arch_base = opts.get("archive_category_name", "📦 Archive")
    everyone = guild

    print("== Build '%s' into \"%s\" (%s)  mode=%s, dry-run=%s =="
          % (cfg.get("name", "server"), gname, guild, mode, a.dry_run))
    for w in warnings:
        print("  note: " + w)

    # --- snapshot what is there now ---
    existing = req("GET", "/guilds/%s/channels" % guild)
    pre_channels = [c for c in existing if c["type"] != CATEGORY]
    pre_categories = [c for c in existing if c["type"] == CATEGORY]
    roles_now = req("GET", "/guilds/%s/roles" % guild)
    role_map = {r["name"]: r["id"] for r in roles_now}
    features = g.get("features") or []
    comm = cfg.get("community") or {}
    do_community = bool(comm) and "COMMUNITY" in features

    # --- what the bot may do, checked before anything is changed ---
    bot_perms = None
    try:
        member = req("GET", "/guilds/%s/members/%s" % (guild, me.get("id")))
        mine = set(str(x) for x in member.get("roles", [])) | {str(guild)}
        bot_perms = 0
        for r in roles_now:
            if str(r["id"]) in mine:
                bot_perms |= int(r.get("permissions", 0))
        if str(g.get("owner_id")) == str(me.get("id")):
            bot_perms = ADMIN
    except DiscordError:
        print("  note: could not read the bot's own permissions; skipping the permission pre-check.")
    is_admin = bot_perms is not None and bool(bot_perms & ADMIN)
    bot_id = None if (is_admin or bot_perms is None) else me.get("id")

    new_roles = [r for r in cfg.get("roles", []) if r["name"] not in role_map]
    need = MANAGE_CHANNELS | VIEW
    if new_roles:
        need |= MANAGE_ROLES
        for r in new_roles:
            need |= int(str(r.get("permissions", "0")))
    if has_overwrites(cfg) or (mode == "archive" and pre_channels):
        need |= MANAGE_ROLES | VIEW | SEND
    if do_community:
        need |= MANAGE_GUILD
    perm_problem = None
    if bot_perms is not None and not is_admin:
        lacking = need & ~bot_perms
        if lacking:
            perm_problem = (
                "The bot is missing permissions this layout needs: %s.\n"
                "  Discord only lets a bot hand out permissions it holds itself, so a layout that creates\n"
                "  an Administrator role needs a bot with Administrator.\n"
                "  Fix: get a new invite link and add the bot again (no need to remove it first):\n"
                "      python3 %s --config %s --client-id %s"
                % (", ".join(names_for(lacking)), os.path.join(SKILL_DIR, "scripts", "permissions.py"),
                   a.config, me.get("id")))
            if not a.dry_run:
                stop(perm_problem + "\n  Nothing in the server was changed.")

    # --- what already matches the layout is reused, never duplicated ---
    def find_category(name):
        return next((c for c in pre_categories if c["name"] == name), None)

    def find_channel(parent_id, name, ctype):
        return next((c for c in pre_channels if c["type"] == ctype and (c.get("parent_id") or None) == parent_id
                     and norm(c["name"], ctype) == norm(name, ctype)), None)

    # --- replace is destructive: demand the server id again before touching anything ---
    reused_preview = set()
    for ch in uncategorised(cfg):
        hit = find_channel(None, display_name(ch, sep), TYPE[ch.get("type", "text")])
        if hit:
            reused_preview.add(hit["id"])
    for cat in cfg.get("categories", []):
        hit_cat = find_category(cat["name"])
        if hit_cat:
            reused_preview.add(hit_cat["id"])
            for ch in cat.get("channels", []):
                hit = find_channel(hit_cat["id"], display_name(ch, sep), TYPE[ch.get("type", "text")])
                if hit:
                    reused_preview.add(hit["id"])
    doomed = [c for c in pre_channels + pre_categories if c["id"] not in reused_preview]
    if mode == "replace" and doomed:
        n_ch = len([c for c in doomed if c["type"] != CATEGORY])
        n_cat = len(doomed) - n_ch
        print("  !! replace will PERMANENTLY DELETE %d channel(s) and %d categor%s from \"%s\", "
              "with all their messages:" % (n_ch, n_cat, "y" if n_cat == 1 else "ies", gname))
        for c in doomed:
            print("       - %s%s" % ("[category] " if c["type"] == CATEGORY else "", c["name"]))
        if not a.dry_run and a.confirm_replace != guild:
            given = "the id given (%s) does not match" % a.confirm_replace if a.confirm_replace \
                else "no confirmation was given"
            stop("Replace was not confirmed (%s). Nothing was changed.\n"
                 "  Deleting channels cannot be undone. If the owner of \"%s\" has said yes to losing the\n"
                 "  channels listed above, run the same command again with this added:\n"
                 "      --confirm-replace %s\n"
                 "  To keep the history instead, use --existing archive." % (given, gname, guild))

    problems = []
    missing_roles = set()

    # --- 1. roles ---
    for r in cfg.get("roles", []):
        if r["name"] in role_map:
            print("  = role exists: %s" % r["name"])
            continue
        body = {"name": r["name"], "hoist": r.get("hoist", False),
                "mentionable": r.get("mentionable", False),
                "permissions": str(r.get("permissions", "0"))}
        col = r.get("colour", r.get("color"))
        if col:
            body["color"] = int(col.lstrip("#"), 16) if isinstance(col, str) else col
        res = req("POST", "/guilds/%s/roles" % guild, body)
        role_map[r["name"]] = res.get("id", "<%s>" % r["name"])
        print("  + role %s" % r["name"])

    # --- 2. categories and channels ---
    key2id = {}          # name in the layout -> channel id (first one wins)
    order = []           # (id, position) for the final reorder
    reused = set()

    def make(item, parent_id, parent_cat, pos):
        ctype = TYPE[item.get("type", "text")]
        nm = display_name(item, sep)
        hit = find_channel(parent_id, nm, ctype)
        if hit:
            cid = hit["id"]
            reused.add(cid)
            print("  = exists: %s" % nm)
        else:
            body = {"name": nm, "type": ctype}
            if parent_id:
                body["parent_id"] = parent_id
            ow = compute_overwrites(item, parent_cat, role_map, everyone, staff_roles, bot_id, missing_roles)
            if ow:
                body["permission_overwrites"] = ow
            res = req("POST", "/guilds/%s/channels" % guild, body)
            cid = res.get("id", "<%s>" % nm)
            print("  + %s" % nm)
        key2id.setdefault(item["name"], cid)
        order.append((cid, pos))

    for i, ch in enumerate(uncategorised(cfg)):      # these float to the top
        make(ch, None, None, i)

    for i, cat in enumerate(cfg.get("categories", [])):
        hit = find_category(cat["name"])
        if hit:
            cid = hit["id"]
            reused.add(cid)
            print("  = category exists: %s" % cat["name"])
        else:
            cbody = {"name": cat["name"], "type": CATEGORY}
            cow = compute_overwrites(cat, None, role_map, everyone, staff_roles, bot_id, missing_roles)
            if cow:
                cbody["permission_overwrites"] = cow
            cres = req("POST", "/guilds/%s/channels" % guild, cbody)
            cid = cres.get("id", "<%s>" % cat["name"])
            print("  + category %s" % cat["name"])
        key2id.setdefault(cat["name"], cid)
        order.append((cid, i))
        for j, ch in enumerate(cat.get("channels", [])):
            make(ch, cid, cat, j)

    for n in sorted(missing_roles):
        print("  note: role %r is named in the layout's access lists but does not exist, so it was skipped." % n)

    # --- 3. Community rules / updates channels (must happen before old ones are moved or deleted) ---
    if comm and not do_community:
        print("  = not a Community server, so the rules / updates channel setting was skipped")
    elif do_community:
        payload = {"features": features}
        for k in ("verification_level", "explicit_content_filter", "default_message_notifications"):
            if g.get(k) is not None:
                payload[k] = g[k]
        if comm.get("rules_channel") in key2id:
            payload["rules_channel_id"] = key2id[comm["rules_channel"]]
        if comm.get("updates_channel") in key2id:
            payload["public_updates_channel_id"] = key2id[comm["updates_channel"]]
        req("PATCH", "/guilds/%s" % guild, payload)
        print("  ~ community rules/updates reassigned")

    # --- 4. the channels that were already there ---
    hide = [{"id": everyone, "type": 0, "allow": "0", "deny": str(VIEW)}]
    if bot_id:
        hide.append({"id": bot_id, "type": 1, "allow": str(VIEW), "deny": "0"})
    new_archives = []

    def attempt(method, path, body, ok_msg, fail_msg):
        try:
            req(method, path, body)
            print(ok_msg)
            return True
        except DiscordError as e:
            print("%s (%s)" % (fail_msg, e.message))
            problems.append("%s (%s)" % (fail_msg.strip(" !"), e.message))
            return False

    if mode == "archive":
        arch_cats = [c for c in pre_categories if is_archive_name(c["name"], arch_base) and c["id"] not in reused]
        arch_ids = {c["id"] for c in arch_cats}
        room = [[c["id"], CATEGORY_LIMIT - len([x for x in pre_channels if x.get("parent_id") == c["id"]])]
                for c in arch_cats]
        to_move = [c for c in pre_channels if c["id"] not in reused and c.get("parent_id") not in arch_ids]
        used_names = {c["name"] for c in pre_categories}
        stuck = set()
        for c in to_move:
            slot = next((s for s in room if s[1] > 0), None)
            if not slot:
                n = 1
                name = arch_base
                while name in used_names:
                    n += 1
                    name = "%s %d" % (arch_base, n)
                used_names.add(name)
                ares = req("POST", "/guilds/%s/channels" % guild,
                           {"name": name, "type": CATEGORY, "permission_overwrites": hide})
                slot = [ares.get("id", "<%s>" % name), CATEGORY_LIMIT]
                room.append(slot)
                new_archives.append(slot[0])
                print("  + %s category (hidden from everyone)" % name)
            if attempt("PATCH", "/channels/%s" % c["id"], {"parent_id": slot[0], "permission_overwrites": hide},
                       "  > archived %s" % c["name"], "  ! could not archive %s" % c["name"]):
                slot[1] -= 1
            else:
                stuck.add(c.get("parent_id"))
        for c in pre_categories:
            if c["id"] in reused or c["id"] in arch_ids:
                continue
            if c["id"] in stuck:
                print("  = left category %s in place (a channel inside it could not be archived)" % c["name"])
                continue
            attempt("DELETE", "/channels/%s" % c["id"], None,
                    "  - removed old (now empty) category %s" % c["name"],
                    "  ! could not remove category %s" % c["name"])
    elif mode == "replace":
        for c in doomed:
            attempt("DELETE", "/channels/%s" % c["id"], None,
                    "  - deleted %s" % c["name"], "  ! could not delete %s" % c["name"])
    elif pre_channels or pre_categories:
        print("  = kept the %d existing channel(s) and categor(ies) as they were" % len(doomed))

    # --- 5. put everything in order ---
    positions = [{"id": cid, "position": pos} for cid, pos in order]
    for k, aid in enumerate(new_archives):
        positions.append({"id": aid, "position": len(cfg.get("categories", [])) + k})
    if positions:
        attempt("PATCH", "/guilds/%s/channels" % guild, positions,
                "  ~ reordered", "  ! could not reorder the channels; drag them into place by hand")

    print("\nDRY RUN COMPLETE. Nothing was changed." if a.dry_run else "\nDONE.")
    print(json.dumps({"created_or_reused": key2id, "roles": role_map}, indent=2, ensure_ascii=False))

    if a.dry_run and perm_problem:
        print("\nWARNING: the real build would stop before changing anything.\n" + perm_problem)
    if problems:
        print("\nFINISHED WITH %d PROBLEM(S):" % len(problems))
        for p in problems:
            print("  - " + p)

    failures = 0
    if a.verify and not a.dry_run:
        print_structure(req, guild)
        failures = check(req, guild, cfg, mode)
    if problems or failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
