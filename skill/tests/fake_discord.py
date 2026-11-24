"""
fake_discord.py: a pretend Discord that lives in memory.

It stands in for urllib.request.urlopen so the real build script runs end to
end without one byte leaving the machine. It keeps a server (channels, roles,
settings), answers the same REST routes the script uses, records every call,
and copies the Discord rules that matter to the build:

  * a bot can only hand out permissions it holds itself (else 403, code 50013)
  * a category holds 50 channels at most
  * a Community server will not let its rules / updates channel be deleted
  * the rules / updates channels only change when the whole Community config is sent
  * a bulk reorder may not move more than one channel between categories
  * text channel names are lower-cased and spaces become hyphens

These rules are written from Discord's documentation. They are a model, not
Discord itself, so a pass here is not proof of a pass on a real server.
"""
import email.message
import io
import json
import re
import urllib.error

API = "https://discord.com/api/v10"
GOOD_TOKEN = "offline-test-token-not-real"
BOT_ID = "900000000000000001"
BOT_ROLE_ID = "900000000000000002"
ADMIN, MANAGE_CHANNELS, MANAGE_GUILD = 1 << 3, 1 << 4, 1 << 5
VIEW, SEND, MANAGE_ROLES = 1 << 10, 1 << 11, 1 << 28
TYPES = {"text": 0, "voice": 2, "category": 4, "news": 5, "stage": 13, "forum": 15}


class _Resp:
    def __init__(self, obj):
        self._b = json.dumps(obj).encode() if obj is not None else b""

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeDiscord:
    def __init__(self, guild_id="111111111111111111", name="Test Server", bot_permissions=ADMIN, community=False):
        self.guild_id = guild_id
        self.name = name
        self.features = ["COMMUNITY", "NEWS"] if community else []
        self.rules_channel_id = None
        self.public_updates_channel_id = None
        self.roles = [
            {"id": guild_id, "name": "@everyone", "permissions": str(VIEW | SEND | 1 << 16), "position": 0},
            {"id": BOT_ROLE_ID, "name": "builder-bot", "permissions": str(bot_permissions),
             "position": 1, "managed": True},
        ]
        self.channels = []
        self._next = 500000000000000000
        self.calls = []          # (method, path, body) for every request received
        self.foreign = []        # requests aimed at anything outside this server
        self.injected = []       # failures to serve before normal handling
        self.network_down = False
        # a second server the bot is NOT asked to touch; it must stay exactly as it is
        self.other_guild_id = "222222222222222222"
        self.other_channel_id = "222222222222222299"

    # ---- helpers for setting up a scenario
    def _id(self):
        self._next += 1
        return str(self._next)

    def add_channel(self, name, kind="text", parent=None, overwrites=None):
        c = {"id": self._id(), "name": name, "type": TYPES[kind], "parent_id": parent,
             "position": len(self.channels), "permission_overwrites": list(overwrites or [])}
        self.channels.append(c)
        return c["id"]

    def inject(self, method, pattern, status, body=None, times=1, headers=None, raw=None):
        """Make the next `times` matching requests fail with this status."""
        self.injected.append({"method": method, "re": re.compile(pattern), "status": status,
                              "body": body, "times": times, "headers": headers or {}, "raw": raw})

    def writes(self):
        return [c for c in self.calls if c[0] != "GET"]

    def snapshot(self):
        return json.dumps([self.channels, self.roles, self.rules_channel_id,
                           self.public_updates_channel_id], sort_keys=True)

    def channel(self, cid):
        return next((c for c in self.channels if c["id"] == cid), None)

    def by_name(self, name):
        return [c for c in self.channels if c["name"] == name]

    # ---- the stand-in for urllib.request.urlopen
    def _fail(self, url, status, code, message, headers=None, raw=None):
        h = email.message.Message()
        for k, v in (headers or {}).items():
            h[k] = str(v)
        payload = raw if raw is not None else json.dumps({"message": message, "code": code})
        raise urllib.error.HTTPError(url, status, message, h, io.BytesIO(payload.encode()))

    def _perms(self):
        p = 0
        for r in self.roles:
            if r["id"] in (self.guild_id, BOT_ROLE_ID):
                p |= int(r["permissions"])
        return p

    def _need(self, url, bits):
        p = self._perms()
        if not p & ADMIN and bits & ~p:
            self._fail(url, 403, 50013, "Missing Permissions")

    def _check_overwrites(self, url, ows):
        if not ows:
            return
        self._need(url, MANAGE_ROLES)
        for ow in ows:
            self._need(url, int(ow.get("allow", 0)) | int(ow.get("deny", 0)))

    def _check_parent(self, url, pid, moving=None):
        if pid is None:
            return
        parent = self.channel(pid)
        if not parent or parent["type"] != 4:
            self._fail(url, 400, 50035, "Invalid Form Body: parent_id is not a category")
        kids = [c for c in self.channels if c["parent_id"] == pid and c["id"] != moving]
        if len(kids) >= 50:
            self._fail(url, 400, 50035, "Maximum number of channels in category reached (50)")

    def urlopen(self, req, *a, **k):
        method, url = req.get_method(), req.full_url
        if self.network_down:
            raise urllib.error.URLError("offline test: network is down")
        if not url.startswith(API):
            self.foreign.append((method, url))
            raise urllib.error.URLError("offline test: refused non-Discord address " + url)
        path = url[len(API):]
        body = json.loads(req.data.decode()) if req.data else None
        self.calls.append((method, path, body))

        if req.get_header("Authorization") != "Bot " + GOOD_TOKEN:
            self._fail(url, 401, 0, "401: Unauthorized")
        for rule in self.injected:
            if rule["times"] > 0 and rule["method"] == method and rule["re"].search(path):
                rule["times"] -= 1
                b = rule["body"] or {}
                self._fail(url, rule["status"], b.get("code", 0), b.get("message", "injected"),
                           rule["headers"],
                           raw=rule["raw"] if rule["raw"] is not None else json.dumps(b))

        g = self.guild_id
        if method == "GET" and path == "/users/@me":
            return _Resp({"id": BOT_ID, "username": "builder-bot", "bot": True})

        m = re.fullmatch(r"/guilds/(\d+)(/.*)?", path)
        if m and m.group(1) != g:
            self.foreign.append((method, path))
            self._fail(url, 404, 10004, "Unknown Guild")
        if m:
            rest = m.group(2) or ""
            if method == "GET" and rest == "":
                return _Resp({"id": g, "name": self.name, "owner_id": "700000000000000001",
                              "features": self.features, "rules_channel_id": self.rules_channel_id,
                              "public_updates_channel_id": self.public_updates_channel_id,
                              "verification_level": 1, "explicit_content_filter": 2,
                              "default_message_notifications": 1})
            if method == "GET" and rest == "/channels":
                return _Resp(self.channels)
            if method == "GET" and rest == "/roles":
                return _Resp(self.roles)
            if method == "GET" and rest == "/members/" + BOT_ID:
                return _Resp({"user": {"id": BOT_ID}, "roles": [BOT_ROLE_ID]})
            if method == "POST" and rest == "/roles":
                self._need(url, MANAGE_ROLES | int(body.get("permissions", 0)))
                if len(self.roles) >= 250:
                    self._fail(url, 400, 30005, "Maximum number of guild roles reached (250)")
                r = {"id": self._id(), "name": body["name"], "permissions": str(body.get("permissions", "0")),
                     "position": 1, "color": body.get("color", 0), "hoist": body.get("hoist", False)}
                self.roles.append(r)
                return _Resp(r)
            if method == "POST" and rest == "/channels":
                self._need(url, MANAGE_CHANNELS)
                if len(self.channels) >= 500:
                    self._fail(url, 400, 30013, "Maximum number of guild channels reached (500)")
                t = body.get("type", 0)
                if t in (5, 13, 15) and "COMMUNITY" not in self.features:
                    self._fail(url, 400, 50035, "Invalid Form Body: channel type needs a Community server")
                self._check_overwrites(url, body.get("permission_overwrites"))
                self._check_parent(url, body.get("parent_id"))
                name = body["name"]
                if t in (0, 5, 15):
                    name = name.lower().replace(" ", "-")
                c = {"id": self._id(), "name": name, "type": t, "parent_id": body.get("parent_id"),
                     "position": len(self.channels),
                     "permission_overwrites": body.get("permission_overwrites", [])}
                self.channels.append(c)
                return _Resp(c)
            if method == "PATCH" and rest == "/channels":
                self._need(url, MANAGE_CHANNELS)
                if len([x for x in body if "parent_id" in x]) > 1:
                    self._fail(url, 400, 40009, "Only one channel can have a parent_id modified at a time")
                for x in body:
                    c = self.channel(x["id"])
                    if not c:
                        self._fail(url, 400, 50035, "Invalid Form Body: unknown channel in reorder")
                for x in body:
                    self.channel(x["id"])["position"] = x["position"]
                return _Resp(None)
            if method == "PATCH" and rest == "":
                self._need(url, MANAGE_GUILD)
                if "features" in body:      # the documented trap: ignored without the full config
                    for key in ("rules_channel_id", "public_updates_channel_id"):
                        if key in body:
                            if not self.channel(body[key]):
                                self._fail(url, 400, 50035, "Invalid Form Body: unknown channel")
                            setattr(self, key, body[key])
                return _Resp({"id": g})

        m = re.fullmatch(r"/channels/(\d+)", path)
        if m:
            c = self.channel(m.group(1))
            if not c:
                self.foreign.append((method, path))
                self._fail(url, 404, 10003, "Unknown Channel")
            self._need(url, MANAGE_CHANNELS)
            if method == "PATCH":
                if "permission_overwrites" in body:
                    self._check_overwrites(url, body["permission_overwrites"])
                if "parent_id" in body:
                    self._check_parent(url, body["parent_id"], moving=c["id"])
                    c["parent_id"] = body["parent_id"]
                if "permission_overwrites" in body:
                    c["permission_overwrites"] = body["permission_overwrites"]
                return _Resp(c)
            if method == "DELETE":
                if "COMMUNITY" in self.features and c["id"] in (self.rules_channel_id,
                                                                self.public_updates_channel_id):
                    self._fail(url, 400, 50074, "Cannot delete a channel required for Community Servers")
                self.channels.remove(c)
                for kid in self.channels:
                    if kid["parent_id"] == c["id"]:
                        kid["parent_id"] = None
                return _Resp(c)

        self.foreign.append((method, path))
        self._fail(url, 404, 0, "404: Not Found")
