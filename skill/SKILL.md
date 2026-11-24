---
name: discord-server-builder
description: >
  Set up, build or restructure a Discord server through the Discord REST API: roles,
  categories, channels, who can see and post where, archiving or replacing the channels
  that are already there, and Community rules and announcements channels. Asks a few
  questions, shows a dry run, builds, then checks its own work. Use when asked to build,
  set up, structure or restructure a Discord server, apply a Discord template, or
  onboard a client's Discord.
category: automation
tags: [skill, discord, onboarding, automation]
version: 1.1.0
allowed-tools:
  - Bash
  - Read
  - Write
  - Edit
  - AskUserQuestion
  - Agent
---

# Discord Server Builder

Build or restructure a Discord server from a layout file. The order never changes:
check setup, look at the server, ask the questions, write the layout, dry run, build, check.

`SKILL_DIR` below is the folder this file lives in (normally `~/.claude/skills/discord-server-builder`).
Everything runs on Python 3.8 or newer with no extra packages.

## Rules that are never broken

1. **Never ask the user to paste the bot token into the chat, and never print it.** They put it in
   `SKILL_DIR/config.env` themselves. Do not `cat`, `grep` or otherwise display that file.
2. **Always dry run first** and show the user the result before building.
3. **Never use `replace` (start fresh) unless the user has said, in their own words in this
   conversation, that they accept every existing channel and its messages being deleted.** The script
   refuses to delete without `--confirm-replace <server id>`. Only add that flag after that explicit
   yes, and show them the list of channels the dry run says will be deleted first.
4. **One server only.** Work on the server id the user gave. Never guess an id.
5. A dry run is a preview, not an offline mode: it reads the token and asks Discord what is in the
   server. It never changes anything.

## Step 0: Check setup

```bash
python3 SKILL_DIR/scripts/discord_build.py --verify
```

This prints the current structure of the server named in `config.env`. If it works, go to Step 2.
If it stops with an error, the message says what is missing. Walk the user through the fix:

| Message | What the user must do |
|---|---|
| No Discord bot token found | Create a bot and put its token in `SKILL_DIR/config.env` (README.md, "Create the bot"). Open the file for them with `open -e SKILL_DIR/config.env`. |
| No server id found | Add `DISCORD_GUILD_ID=` to `config.env` (README.md, "Find your server id"). |
| Discord rejected the bot token | Reset the token in the Developer Portal and paste the new one into `config.env`. |
| The bot cannot see server | Invite the bot (Step 1), or correct the server id. |
| Python could not verify Discord's security certificate | Run `Install Certificates.command` from the Python folder in Applications, or use `/usr/bin/python3`. |

If `config.env` does not exist, create it without overwriting anything:
`cp -n SKILL_DIR/config.example.env SKILL_DIR/config.env && chmod 600 SKILL_DIR/config.env`

## Step 1: Invite the bot (skip if Step 0 printed the server)

Creating the bot and copying its token must be done by the user in their own browser, because it
needs their Discord login. README.md has the click-by-click steps. Once they have the Application ID:

```bash
python3 SKILL_DIR/scripts/permissions.py --config SKILL_DIR/scripts/templates/team-ops.json --client-id <APP_ID>
```

This prints the invite link with the right permissions number for that layout. The user opens the
link, picks the server, and clicks Continue then Authorise. They must have Manage Server on that
server. No gateway intents are needed: leave all three Privileged Gateway Intents switched off.

Which number to expect:

- **8 (Administrator)** for the three stock templates, because each creates a role that itself has
  Administrator. Discord only lets a bot hand out permissions it holds.
- **268438576** (View Channels, Send Messages, Manage Channels, Manage Roles, Manage Server) for a
  layout whose roles carry no permissions of their own.

The bot can be removed from the server once the build is finished.

## Step 2: Look at the server first (always, before changing anything)

```bash
python3 SKILL_DIR/scripts/discord_build.py --verify
```

Note the existing channels and roles, whether `features` contains `COMMUNITY`, and any channels that
belong to other bots or integrations (ticket tools, stats counters, levelling bots, automation
feeds). Tell the user about those before archiving or replacing: they may stop working.

## Step 3: Ask the questions

Use `AskUserQuestion`. The ones that decide the build:

1. **Starting template**: `team-ops` (internal sales or agency team), `community` (public community),
   `coaching-client` (client programme portal), or custom.
2. **What about the old channels?**
   - **Keep** (`keep`): leave them exactly as they are and add the new layout alongside.
   - **Archive** (`archive`): move them into a hidden "📦 Archive" category. Messages are kept. The
     safest way to tidy up.
   - **Start fresh** (`replace`): delete them, messages and all. Cannot be undone. See rule 3.
3. **Naming style**: emoji plus separator (for example `💬・general`) or plain names. Match what the
   server already uses where you can.
4. **Roles**: which team or member roles, and which are shown separately in the member list.
5. **Private and read-only channels**: which channels only staff can see, which only staff can post in.
6. **Branding**: the script never changes the server name or icon.

## Step 4: Write the layout file

Copy a template from `SKILL_DIR/scripts/templates/` to a working file (for example
`/tmp/<client>-discord.json`) and tailor it. Do not edit the templates themselves.

```jsonc
{
  "name": "label for the printout",
  "guild_id": "",                       // leave empty to use the server id from config.env
  "options": { "separator": "・", "existing_channels": "keep | archive | replace",
               "archive_category_name": "📦 Archive", "staff_roles": ["Founder", "Manager"] },
  "roles": [ { "name": "Founder", "colour": "F1C40F", "hoist": true, "mentionable": true, "permissions": "8" } ],
  "uncategorised": [ { "name": "rules", "emoji": "✅", "type": "text", "readonly": true } ],
  "categories": [ { "name": "📢 Information", "private": false,
      "channels": [ { "name": "announcements", "emoji": "🚨", "type": "text", "readonly": true } ] } ],
  "community": { "rules_channel": "rules", "updates_channel": "announcements" }
}
```

- `type`: `text` or `voice`. (`news`, `stage` and `forum` only work on Community servers.)
- `readonly`: nobody can post except `post_roles`, or `staff_roles` if that is not given.
- `private`: nobody can see it except `allow_roles`, or `staff_roles` if that is not given. On a
  category it applies to every channel inside.
- `permissions` on a role is Discord's permissions number as a string. `"0"` adds nothing, `"8"` is
  Administrator.
- A role named in `staff_roles` that does not exist is skipped, and the script says so.
- `community` is ignored, with a note, on a server that is not a Community server.
- At most 50 channels per category. A channel name may appear once per category.

## Step 5: Dry run, then build

```bash
python3 SKILL_DIR/scripts/discord_build.py --config /tmp/<client>-discord.json --dry-run
```

Show the user what it lists: roles, categories, channels, what happens to the old channels. If it
ends with a WARNING about missing permissions, fix that first (Step 1). When the user agrees:

```bash
python3 SKILL_DIR/scripts/discord_build.py --config /tmp/<client>-discord.json --verify
```

For start fresh only, and only after rule 3 is satisfied, add `--confirm-replace <server id>`.

What the build does, in order: creates the roles, creates the categories and channels with their
permissions, points a Community server at its new rules and announcements channels, deals with the
old channels, reorders, then prints the structure and a PASS or FAIL line for every item in the layout.

If it stops part way, read the message, fix the cause and run the same command again. It finds what
it already created and reuses it, so nothing is duplicated.

## Step 6: Check and report

The build's own check ends with `CHECK RESULT: N passed, 0 failed`. To check again later without
changing anything:

```bash
python3 SKILL_DIR/scripts/discord_build.py --config /tmp/<client>-discord.json --check
```

Report to the user what was built, plus any follow-ups:

- New roles are created at the bottom of the role list and nobody is given them. The user assigns
  roles to people and drags them into order in Server Settings, Roles.
- Archived channels lose their old individual permissions and become hidden from everyone except
  administrators. Channels fed by other bots or integrations may need reconnecting.
- If the bot is no longer needed, the user can remove it from the server.

## Notes

- Run one build at a time per server. Do not run builds in parallel.
- Exit codes: 0 fine, 1 the build or check hit a problem, 2 a setup problem and nothing was changed.
- To prove the scripts work without touching Discord: `python3 SKILL_DIR/tests/run_offline_tests.py`
- Every endpoint used and every known trap: `SKILL_DIR/references/api-cookbook.md`.
