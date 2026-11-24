# Discord Server Builder (a Claude Code skill)

Tell Claude Code what your team looks like and it builds your Discord server for you: roles,
categories, channels, and who can see and post where. It asks a few questions, shows you a dry run
of exactly what it will do, builds it, then checks its own work line by line.

## What it does

- Starts from one of three templates, or a layout you describe:
  - `team-ops`: an internal sales or agency team (announcements, sales channels, end of day reports, payments, a private leadership area, voice rooms)
  - `community`: a public community (rules, announcements, conversation channels, private one-to-one channels)
  - `coaching-client`: a client programme (start here, programme resources, community, support, a private staff area)
- Creates the roles, then the categories and channels, with private and read-only channels locked correctly.
- Deals with the channels you already have in one of three ways that you choose (see "The three options").
- On a Community server, points Discord at the new rules and announcements channels.
- Finishes with a PASS or FAIL line for every role, category and channel.

It does not change your server name or icon, does not message anyone, does not give roles to people,
and does not touch any server other than the one whose id you give it.

## What you need

- A Mac with [Claude Code](https://claude.com/claude-code) installed and working.
- Python 3.8 or newer. Macs have it once Apple's command line tools are installed. Check by typing
  `python3 --version` in Terminal. If it is missing, run `xcode-select --install`. No extra Python
  packages are needed.
- A Discord account, and a server where you are the owner or have the Manage Server permission.
- Ten minutes to create a bot (free). Steps below.

## Install

1. Unzip the folder you were sent.
2. Open Terminal, type `bash ` (with the space), drag `install.sh` into the window and press Return.

The installer copies the skill to `~/.claude/skills/discord-server-builder`, creates your settings
file `config.env` there, and runs the built-in offline tests. If you install again later it keeps
your existing `config.env` untouched.

## Create the bot

The skill works through a Discord bot that you own. Nobody else has access to it.

1. Go to <https://discord.com/developers/applications> and log in.
2. Click **New Application**, give it a name such as "Server Builder", tick the box and click **Create**.
3. On the **General Information** page, copy the **Application ID**. Keep it for the invite step.
4. Click **Bot** in the left menu.
5. Click **Reset Token**, confirm, and click **Copy**. This long string is the bot token. Discord shows
   it once. Treat it like a password: anyone who has it controls your bot.
6. On the same page, leave all three **Privileged Gateway Intents** switched **off** (Presence, Server
   Members, Message Content). The skill does not need any of them.
7. Leave everything else as it is.

## Find your server id

1. In Discord, open **User Settings**, then **Advanced**, and switch on **Developer Mode**.
2. Right-click your server's icon in the left bar and choose **Copy Server ID**. It is 17 to 20 digits.

## Fill in your settings

In Terminal:

```bash
open -e ~/.claude/skills/discord-server-builder/config.env
```

Replace the two placeholders with your token and your server id, so the file reads:

```
DISCORD_BOT_TOKEN=the-long-token-you-copied
DISCORD_GUILD_ID=123456789012345678
```

Save and close. Do not paste the token into the Claude chat, and do not share this file.

If you prefer, you can set these as environment variables instead (`DISCORD_BOT_TOKEN` and
`DISCORD_GUILD_ID`). Environment variables win over the file.

## Invite the bot

The bot needs permission to build. In Terminal, with your Application ID in place of the digits:

```bash
python3 ~/.claude/skills/discord-server-builder/scripts/permissions.py \
  --config ~/.claude/skills/discord-server-builder/scripts/templates/team-ops.json \
  --client-id 123456789012345678
```

It prints an invite link. Open it in your browser, choose your server under **Add to server**, click
**Continue**, then **Authorise** (shown as "Authorize" if your Discord is set to US English).

The exact permissions the bot is given:

| Layout | Permissions number | What that grants |
|---|---|---|
| Any of the three templates | `8` | Administrator |
| A custom layout whose roles have no permissions of their own | `268438576` | View Channels, Send Messages, Manage Channels, Manage Roles, Manage Server |

Why Administrator for the templates: each template creates a top role (Founder or Admin) that has
Administrator, and Discord only lets a bot hand out permissions it holds itself. If you would rather
not give the bot Administrator, ask Claude to set that role's `permissions` to `"0"` in your layout
and give the role its powers by hand afterwards. The link printed for your layout will then use the
smaller number.

The invite link asks for the `bot` scope only. You can remove the bot from your server as soon as
the build is finished.

## Check your setup

```bash
python3 ~/.claude/skills/discord-server-builder/scripts/discord_build.py --verify
```

This only reads. It should print your server's name and its current channels. If it prints an error
instead, the message tells you what to fix, and "Troubleshooting" below covers each one.

## Your first dry run

Open Claude Code and say something like:

> Set up my Discord server. We are a sales team: me as founder, one manager, two closers and three setters.

Claude asks which template to start from, what to do with the old channels, how to name things and
which channels are private. It then runs a dry run and shows you every role, category and channel it
would create and what would happen to each existing channel. Nothing is changed at this point.

A dry run is a preview, not an offline mode. It uses your token to ask Discord what is in the server
so the preview is accurate. It never sends a request that changes anything.

You can run one yourself:

```bash
cd ~/.claude/skills/discord-server-builder
python3 scripts/discord_build.py --config scripts/templates/team-ops.json --existing keep --dry-run
```

When you are happy, tell Claude to go ahead. It builds, then prints the finished structure and
`CHECK RESULT: N passed, 0 failed`.

## The three options for the channels you already have

| Option | In the layout file | What happens to existing channels | Can it be undone? |
|---|---|---|---|
| **Keep** | `keep` | Nothing. Every existing channel, category and message stays exactly as it is. The new layout is added alongside. | Nothing to undo. |
| **Archive** | `archive` | Every existing channel is moved into a category called "📦 Archive" that is hidden from everyone except administrators. All messages are kept. The old categories, now empty, are removed. Each archived channel's own permission settings are replaced by "hidden from everyone". More than 50 channels spill into "📦 Archive 2". | Mostly. Drag a channel back out and set its permissions again. |
| **Start fresh** | `replace` | Every existing channel and category is **deleted, with all of its messages**. | **No.** Discord cannot restore a deleted channel. |

Safety built in:

- Start fresh will not run unless the command includes `--confirm-replace` followed by your server
  id. Claude is instructed to add it only after you have said, in so many words, that you accept
  losing those channels. The dry run lists every channel that would be deleted.
- The new channels are always created before any old channel is moved or deleted.
- On a Community server, Discord refuses to delete the current rules and updates channels. If your
  layout does not include replacements for them, those two channels are left in place and the build
  tells you so.
- If an existing channel already matches one in the layout (same name, same category), it is reused
  rather than duplicated, archived or deleted. This also means running the same build twice is safe.
- Channels that other bots post into (ticket tools, stats counters, automation feeds) may stop
  working once archived or deleted. Check before you choose.

## After the build

- New roles are created but not given to anyone. Assign them in Server Settings, Members.
- New roles appear at the bottom of the role list. Drag them into the order you want in Server
  Settings, Roles.
- To check the server against the layout again at any time, without changing anything, ask Claude
  to "check my Discord against the layout".

## Test it without touching Discord

```bash
python3 ~/.claude/skills/discord-server-builder/tests/run_offline_tests.py
```

This runs the real build script against a pretend Discord held in memory: empty servers, servers
with existing channels, all three options, a bot with too few permissions, rate limiting, a wrong
token. It uses a made-up token and a made-up server, does not read your settings file, and blocks
all network connections while it runs. Expect `38 passed, 0 failed`. Add `-v` to see every command
and its output.

The pretend Discord follows Discord's published rules. It is a model, so passing it is strong
evidence the logic is right, not a guarantee about the real service. Always dry run on your real
server first.

## Troubleshooting

| What you see | What it means and what to do |
|---|---|
| `No Discord bot token found` | `config.env` is missing, or still has the placeholder. Fill it in ("Fill in your settings"). |
| `No server id found` or `does not look right` | Add your server id to `config.env`. It is digits only, 17 to 20 of them. |
| `Discord rejected the bot token (401)` | The token is wrong or was reset. In the Developer Portal: Bot, Reset Token, copy, paste into `config.env`. |
| `The bot cannot see server ...` | The bot has not been invited to that server, or the id is for a different server. Do "Invite the bot" again and re-check the id. |
| `The bot is missing permissions this layout needs` | The bot was invited with too little. Run the `permissions.py` command it prints, open the new link and authorise again. No need to remove the bot first. Nothing was changed in your server. |
| `Discord refused: the bot is missing a permission (403)` part way through | Usually the bot's role sits below a role it needs to manage. In Server Settings, Roles, drag the bot's role to the top, then run the same command again. Work already done is reused, not duplicated. |
| `Replace was not confirmed` | Start fresh needs `--confirm-replace` and your server id. This is deliberate. Nothing was changed. |
| `rate limited by Discord, waiting ...` | Normal on a large build. It waits and carries on by itself. |
| `Discord is rate limiting this bot` and it stops | Wait a few minutes and run the same command again. |
| `could not delete rules` (or the updates channel) | A Community server must always have these. Use the `community` template, which hands them over to new channels first, or leave them. |
| `Python could not verify Discord's security certificate` | You are using Python from python.org. Open Applications, the Python folder, and double-click "Install Certificates.command". Or run the command with `/usr/bin/python3` in place of `python3`. |
| `Could not reach Discord` | No internet connection, or Discord is down. Try again shortly. |
| `python3: command not found` | Run `xcode-select --install`, wait for it to finish, try again. |
| Claude does not seem to know the skill | Check the folder `~/.claude/skills/discord-server-builder` contains `SKILL.md`, then quit and reopen Claude Code. |
| `The layout file has N problem(s)` | The layout Claude wrote has a mistake, listed underneath. Paste the message to Claude and ask it to fix the layout. |

## Uninstall

Delete the folder `~/.claude/skills/discord-server-builder`. To retire the bot, remove it from your
server (right-click it, Kick) and delete the application in the Developer Portal.

## What is in this folder

```
README.md                 this file
install.sh                the installer
skill/
  SKILL.md                instructions Claude follows
  config.example.env      template for your settings file
  scripts/
    discord_build.py      the builder (dry run, build, verify, check)
    permissions.py        works out the invite link
    templates/            team-ops.json, community.json, coaching-client.json
  references/
    api-cookbook.md       every Discord request used, and the known traps
  tests/
    run_offline_tests.py  offline tests you can run
    fake_discord.py       the pretend Discord they run against
```
