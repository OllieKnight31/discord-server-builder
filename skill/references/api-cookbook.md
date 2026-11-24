# Discord API cookbook and known traps

Base URL: `https://discord.com/api/v10`. Every request needs the header `Authorization: Bot <token>`
and a `User-Agent`. The scripts send these for you. Never put the token on a command line or in the chat.

## Requests the build script makes

| When | Request |
|---|---|
| Always, first | `GET /users/@me`, `GET /guilds/{id}`, `GET /guilds/{id}/channels`, `GET /guilds/{id}/roles`, `GET /guilds/{id}/members/{bot id}` |
| New role | `POST /guilds/{id}/roles` |
| New category or channel | `POST /guilds/{id}/channels` |
| Community rules and updates | `PATCH /guilds/{id}` |
| Archive an old channel | `PATCH /channels/{channel id}` |
| Remove an emptied category, or delete in replace mode | `DELETE /channels/{channel id}` |
| Final order | `PATCH /guilds/{id}/channels` |

A dry run, `--verify` on its own and `--check` only ever send the GET requests.

## Reading a server

- `GET /guilds/{id}`: name, `owner_id`, `features` (look for `COMMUNITY`), `rules_channel_id`,
  `public_updates_channel_id`, `verification_level`, `explicit_content_filter`,
  `default_message_notifications`.
- `GET /guilds/{id}/channels`: a list. `type` 0 text, 2 voice, 4 category, 5 news, 13 stage, 15 forum.
  `parent_id` is the category. Threads are not included.
- `GET /guilds/{id}/roles`: `position` (higher is more senior), `managed: true` marks a bot's own role,
  `permissions` is a number in a string.

## Permission overwrites

Each entry: `{ "id": "<role or member id>", "type": 0 or 1, "allow": "<bits>", "deny": "<bits>" }`
(0 is a role, 1 is a member). `VIEW_CHANNEL = 1024`, `SEND_MESSAGES = 2048`.

- The `@everyone` role id is the same as the server id.
- Read-only channel: deny 2048 for @everyone, allow 2048 for the staff roles.
- Private channel: deny 1024 for @everyone, allow 1024 for the roles that should see it.
- Channels do not follow their category's overwrites unless they are synced, so the script sets the
  overwrite on each channel as well.
- A bot that is not an administrator loses sight of a channel it hides from @everyone, so the script
  adds an overwrite letting the bot itself keep View Channel on hidden channels.

## Known traps

1. **A bot cannot hand out a permission it does not hold.** Creating a role with Administrator
   needs a bot with Administrator. Setting an overwrite for a permission needs the bot to hold that
   permission. Otherwise: `403 Missing Permissions (50013)`. The script checks this before it changes
   anything and tells you which permissions are missing.

2. **Community rules and updates channels ignore a small PATCH.** `PATCH /guilds/{id}` with only
   `rules_channel_id` and `public_updates_channel_id` returns 200 and changes nothing. The whole
   Community config must be sent together:
   ```json
   { "features": ["COMMUNITY", "NEWS"],
     "rules_channel_id": "...", "public_updates_channel_id": "...",
     "verification_level": 1, "explicit_content_filter": 2, "default_message_notifications": 1 }
   ```
   A Community server must have valid rules and updates channels at all times, so the new ones are
   set before the old ones are archived or deleted. Discord refuses to delete a channel that is still
   the rules or updates channel.

3. **Role order.** A bot can only edit, delete or assign roles that sit below its own role, even
   with Administrator. New roles are created at the bottom. To let the bot manage a role above it, a
   person must drag the bot's role higher in Server Settings, Roles.

4. **Bulk reorder cannot move channels between categories.** `PATCH /guilds/{id}/channels` takes a
   list of `{id, position}`. Adding `parent_id` for more than one channel returns
   `400 (40009)`. So each move is its own `PATCH /channels/{id}` and the positions go in one call.

5. **Rate limits (429).** The reply carries `retry_after` in seconds. The script waits that long plus
   half a second and sends the same request again, up to eight times, and pauses 0.4 seconds after
   every change. Creating and deleting channels are the tightest limits.

6. **Limits.** 50 channels per category (the archive spills into "Archive 2" and so on), 500 channels
   and 250 roles per server.

7. **Creating a bot or resetting its token needs a person.** The Developer Portal sits behind the
   user's Discord login and two-factor check. There is also no way to add a bot to a server without
   a person clicking Authorise once.

## Permission numbers

Work them out with `scripts/permissions.py` (`--preset builder|admin`, `--config`, `--client-id`, `--extra`).

| Preset | Number | Includes |
|---|---|---|
| builder | `268438576` | VIEW_CHANNEL, SEND_MESSAGES, MANAGE_CHANNELS, MANAGE_ROLES, MANAGE_GUILD |
| admin | `8` | ADMINISTRATOR |

`builder` is enough only when the layout's roles carry no permissions of their own. Pass `--config`
and the script adds what the layout's roles need, or switches to `8` if any role has Administrator.

## Invite link

`https://discord.com/api/oauth2/authorize?client_id=<APP_ID>&scope=bot&permissions=<NUMBER>&integration_type=0`

In the browser: pick the server under "Add to server", Continue, then Authorise (Discord may show a
captcha). The person doing this needs Manage Server on that server.
