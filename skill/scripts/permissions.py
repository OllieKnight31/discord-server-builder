#!/usr/bin/env python3
"""
permissions.py: work out the permissions number for the bot invite link.

USAGE:
  python3 permissions.py                                   # builder preset
  python3 permissions.py --client-id <APP_ID>              # also print the invite link
  python3 permissions.py --config templates/team-ops.json --client-id <APP_ID>
  python3 permissions.py --preset admin --client-id <APP_ID>
  python3 permissions.py --extra MANAGE_MESSAGES,MANAGE_WEBHOOKS --client-id <APP_ID>

PRESETS:
  builder  least privilege. Enough to create channels, categories and roles that
           carry no special permissions of their own.
  admin    Administrator. Needed whenever the layout creates a role that itself
           has Administrator (all three stock templates do).

WHY --config MATTERS:
  Discord only lets a bot hand out permissions the bot holds itself. If a layout
  creates a role with Administrator, or with Kick Members, the bot must have that
  too or Discord answers 403 Missing Permissions. Pass the layout with --config
  and this script adds whatever its roles need.

  The role order rule still applies: a bot can only manage roles that sit below
  its own role in Server Settings, Roles.
"""
import argparse
import json
import sys

PERMS = {
    "CREATE_INSTANT_INVITE": 1 << 0,
    "KICK_MEMBERS": 1 << 1,
    "BAN_MEMBERS": 1 << 2,
    "ADMINISTRATOR": 1 << 3,
    "MANAGE_CHANNELS": 1 << 4,
    "MANAGE_GUILD": 1 << 5,
    "ADD_REACTIONS": 1 << 6,
    "VIEW_AUDIT_LOG": 1 << 7,
    "PRIORITY_SPEAKER": 1 << 8,
    "STREAM": 1 << 9,
    "VIEW_CHANNEL": 1 << 10,
    "SEND_MESSAGES": 1 << 11,
    "SEND_TTS_MESSAGES": 1 << 12,
    "MANAGE_MESSAGES": 1 << 13,
    "EMBED_LINKS": 1 << 14,
    "ATTACH_FILES": 1 << 15,
    "READ_MESSAGE_HISTORY": 1 << 16,
    "MENTION_EVERYONE": 1 << 17,
    "USE_EXTERNAL_EMOJIS": 1 << 18,
    "VIEW_GUILD_INSIGHTS": 1 << 19,
    "CONNECT": 1 << 20,
    "SPEAK": 1 << 21,
    "MUTE_MEMBERS": 1 << 22,
    "DEAFEN_MEMBERS": 1 << 23,
    "MOVE_MEMBERS": 1 << 24,
    "USE_VAD": 1 << 25,
    "CHANGE_NICKNAME": 1 << 26,
    "MANAGE_NICKNAMES": 1 << 27,
    "MANAGE_ROLES": 1 << 28,
    "MANAGE_WEBHOOKS": 1 << 29,
    "MANAGE_GUILD_EXPRESSIONS": 1 << 30,
    "USE_APPLICATION_COMMANDS": 1 << 31,
    "REQUEST_TO_SPEAK": 1 << 32,
    "MANAGE_EVENTS": 1 << 33,
    "MANAGE_THREADS": 1 << 34,
    "CREATE_PUBLIC_THREADS": 1 << 35,
    "CREATE_PRIVATE_THREADS": 1 << 36,
    "USE_EXTERNAL_STICKERS": 1 << 37,
    "SEND_MESSAGES_IN_THREADS": 1 << 38,
    "USE_EMBEDDED_ACTIVITIES": 1 << 39,
    "MODERATE_MEMBERS": 1 << 40,
    "VIEW_CREATOR_MONETIZATION_ANALYTICS": 1 << 41,
    "USE_SOUNDBOARD": 1 << 42,
    "CREATE_GUILD_EXPRESSIONS": 1 << 43,
    "CREATE_EVENTS": 1 << 44,
    "USE_EXTERNAL_SOUNDS": 1 << 45,
    "SEND_VOICE_MESSAGES": 1 << 46,
    "SEND_POLLS": 1 << 49,
    "USE_EXTERNAL_APPS": 1 << 50,
}

ADMINISTRATOR = PERMS["ADMINISTRATOR"]

PRESETS = {
    "builder": ["VIEW_CHANNEL", "SEND_MESSAGES", "MANAGE_CHANNELS", "MANAGE_ROLES", "MANAGE_GUILD"],
    "admin": ["ADMINISTRATOR"],
}


def names_for(value):
    """Turn a permissions number back into a list of names."""
    out = [n for n, bit in PERMS.items() if value & bit]
    known = 0
    for bit in PERMS.values():
        known |= bit
    rest = value & ~known
    if rest:
        out.append("UNKNOWN_BITS_%d" % rest)
    return out


def compute(names):
    total = 0
    for n in names:
        if n not in PERMS:
            raise SystemExit("Unknown permission: %s. Known: %s" % (n, ", ".join(sorted(PERMS))))
        total |= PERMS[n]
    return total


def role_permissions_in(config_path):
    """Every permission bit carried by the roles a layout would create."""
    try:
        with open(config_path, encoding="utf-8") as f:
            cfg = json.load(f)
    except OSError as e:
        raise SystemExit("Cannot read layout file %s: %s" % (config_path, e))
    except ValueError as e:
        raise SystemExit("Layout file %s is not valid JSON: %s" % (config_path, e))
    total = 0
    for r in cfg.get("roles", []):
        try:
            total |= int(str(r.get("permissions", "0")))
        except ValueError:
            raise SystemExit("Role %r has a permissions value that is not a number" % r.get("name"))
    return total


def main():
    ap = argparse.ArgumentParser(description="Work out the permissions number and invite link for the bot.")
    ap.add_argument("--preset", choices=list(PRESETS), default="builder")
    ap.add_argument("--client-id", help="the Application ID from the Discord Developer Portal")
    ap.add_argument("--config", help="a layout JSON file; adds whatever its roles need")
    ap.add_argument("--extra", default="", help="comma-separated extra permission names")
    a = ap.parse_args()

    names = list(PRESETS[a.preset])
    if a.extra:
        names += [x.strip() for x in a.extra.split(",") if x.strip()]
    value = compute(names)

    note = ""
    if a.config:
        role_bits = role_permissions_in(a.config)
        if role_bits & ADMINISTRATOR:
            value = ADMINISTRATOR
            note = ("This layout creates a role with Administrator, so the bot needs "
                    "Administrator itself (Discord will not let a bot hand out more than it has).")
        else:
            value |= role_bits

    print("preset:      %s" % a.preset)
    print("permissions: %d" % value)
    print("includes:    %s" % ", ".join(names_for(value)))
    if note:
        print("note:        %s" % note)
    if a.client_id:
        if not a.client_id.isdigit():
            sys.exit("--client-id must be the Application ID, which is digits only.")
        print("invite URL:  https://discord.com/api/oauth2/authorize?client_id=%s"
              "&scope=bot&permissions=%d&integration_type=0" % (a.client_id, value))
    else:
        print("(pass --client-id <APP_ID> to also print the invite link)")


if __name__ == "__main__":
    main()
