#!/usr/bin/env python3
"""
run_offline_tests.py: prove the build script behaves, with no network at all.

    python3 tests/run_offline_tests.py          # summary
    python3 tests/run_offline_tests.py -v       # also show each script's output

Runs the real discord_build.py against the pretend Discord in fake_discord.py.
Nothing is sent to Discord, your settings file is not read, and your real bot
token is never touched: every run uses a made-up token and a made-up server.
Outgoing network connections are blocked for the length of the test as a
second safety net.

A pass here shows the script's logic is sound. It is not a test against the
real Discord, which no offline test can be.
"""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import socket
import sys
import tempfile
import time
import urllib.request

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fake_discord as fd  # noqa: E402
from fake_discord import FakeDiscord, GOOD_TOKEN, BOT_ID  # noqa: E402

args = [x for x in sys.argv[1:] if x != "-v"]
VERBOSE = "-v" in sys.argv[1:]
SCRIPTS = os.path.abspath(args[0]) if args else os.path.join(os.path.dirname(HERE), "scripts")
TEMPLATES = os.path.join(SCRIPTS, "templates")
G = "111111111111111111"
TMP = tempfile.mkdtemp(prefix="dsb-test-")
NO_SETTINGS = os.path.join(TMP, "no-such-config.env")
BUILDER_PERMS = 268438576
_n = [0]


class Result:
    def __init__(self, code, out, err, sleeps):
        self.code, self.out, self.err, self.sleeps = code, out, err, sleeps
        self.all = out + err


def _blocked(*a, **k):
    raise RuntimeError("offline test: a real network connection was attempted and blocked")


def run(fake, argv, script="discord_build.py", env=None, settings=NO_SETTINGS):
    """Run one script's main() against the fake. Returns exit code and captured output."""
    env = {"DISCORD_BOT_TOKEN": GOOD_TOKEN} if env is None else env
    saved_env = dict(os.environ)
    for k in list(os.environ):
        if k.startswith("DISCORD_"):
            del os.environ[k]
    os.environ.update(env)
    saved = (urllib.request.urlopen, time.sleep, socket.socket.connect, sys.argv)
    sleeps = []
    urllib.request.urlopen = fake.urlopen if fake else _blocked
    time.sleep = sleeps.append
    socket.socket.connect = _blocked
    full = [script] + list(argv)
    if script == "discord_build.py" and settings:
        full += ["--env-file", settings]
    sys.argv = full
    out, err = io.StringIO(), io.StringIO()
    code = 0
    try:
        _n[0] += 1
        spec = importlib.util.spec_from_file_location("dsb_run_%d" % _n[0], os.path.join(SCRIPTS, script))
        mod = importlib.util.module_from_spec(spec)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                spec.loader.exec_module(mod)
                mod.main()
            except SystemExit as e:
                if isinstance(e.code, str):
                    print(e.code, file=sys.stderr)
                    code = 1
                else:
                    code = e.code or 0
    finally:
        urllib.request.urlopen, time.sleep, socket.socket.connect, sys.argv = saved
        os.environ.clear()
        os.environ.update(saved_env)
    r = Result(code, out.getvalue(), err.getvalue(), sleeps)
    if VERBOSE:
        print("    $ " + " ".join(full) + "   -> exit %s" % code)
        for line in (r.out + r.err).splitlines():
            print("      | " + line)
    assert GOOD_TOKEN not in r.all, "the token was printed"
    assert "Traceback" not in r.all, "stack trace shown to the user:\n" + r.err[-600:]
    return r


def tpl(name):
    return os.path.join(TEMPLATES, name + ".json")


def layout(obj, name="custom.json"):
    p = os.path.join(TMP, name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    return p


def empty_server(**kw):
    return FakeDiscord(guild_id=G, name="Acme HQ", **kw)


def lived_in_server(community=False, **kw):
    """An invented server that already has channels, the way Discord creates one."""
    f = FakeDiscord(guild_id=G, name="Acme HQ", community=community, **kw)
    t = f.add_channel("Text Channels", "category")
    v = f.add_channel("Voice Channels", "category")
    f.add_channel("general", "text", t)
    f.add_channel("off-topic", "text", t)
    f.add_channel("General", "voice", v)
    f.add_channel("welcome", "text")
    if community:
        f.rules_channel_id = f.add_channel("rules", "text")
        f.public_updates_channel_id = f.add_channel("moderator-only", "text")
    return f


def ids(fake):
    return {c["id"] for c in fake.channels}


def wanted_names(path):
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    sep = cfg["options"].get("separator", "・")
    names = []
    for ch in cfg.get("uncategorised", []):
        names.append((ch.get("emoji") + sep if ch.get("emoji") else "") + ch["name"])
    for cat in cfg["categories"]:
        names.append(cat["name"])
        for ch in cat["channels"]:
            names.append((ch.get("emoji") + sep if ch.get("emoji") else "") + ch["name"])
    return cfg, names


def assert_sane_order(fake, old_ids=()):
    """Roles first, a parent before its children, old channels touched only after the new ones exist,
    Community settings before anything old is moved or deleted, reorder last."""
    w = fake.writes()
    kinds = []
    for method, path, body in w:
        if method == "POST" and path.endswith("/roles"):
            kinds.append("role")
        elif method == "POST" and path.endswith("/channels"):
            kinds.append("archive-cat" if body["type"] == 4 and "Archive" in body["name"] else "create")
        elif method == "PATCH" and path == "/guilds/%s" % G:
            kinds.append("community")
        elif method == "PATCH" and path == "/guilds/%s/channels" % G:
            kinds.append("reorder")
        elif path.startswith("/channels/"):
            assert path.split("/")[2] in old_ids, "touched a channel that did not exist before: " + path
            kinds.append("old")
        else:
            raise AssertionError("unexpected call %s %s" % (method, path))
    rank = {"role": 0, "create": 1, "community": 2, "archive-cat": 3, "old": 3, "reorder": 4}
    seq = [rank[k] for k in kinds]
    assert seq == sorted(seq), "calls out of order: %s" % kinds
    assert kinds.count("reorder") <= 1 and kinds.count("community") <= 1
    assert not fake.foreign, "a request left the named server: %s" % fake.foreign
    for method, path, body in fake.calls:
        assert path == "/users/@me" or path.startswith("/guilds/%s" % G) or path.startswith("/channels/"), path


TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


# ------------------------------------------------------------ setup problems

@test
def missing_token_gives_clear_error_and_sends_nothing():
    f = empty_server()
    r = run(f, ["--config", tpl("team-ops"), "--guild", G, "--dry-run"], env={})
    assert r.code == 2 and "No Discord bot token found" in r.err and "config.env" not in r.out
    assert NO_SETTINGS in r.err and not f.calls


@test
def placeholder_token_in_settings_file_is_treated_as_missing():
    f = empty_server()
    s = os.path.join(TMP, "placeholder.env")
    shutil.copy(os.path.join(os.path.dirname(SCRIPTS), "config.example.env"), s)
    r = run(f, ["--config", tpl("team-ops"), "--guild", G, "--dry-run"], env={}, settings=s)
    assert r.code == 2 and "still the placeholder" in r.err and not f.calls


@test
def settings_file_supplies_token_and_server_id():
    f = empty_server()
    s = os.path.join(TMP, "good.env")
    with open(s, "w") as fh:
        fh.write("# comment\nexport DISCORD_BOT_TOKEN=\"%s\"\nDISCORD_GUILD_ID='%s'\n" % (GOOD_TOKEN, G))
    r = run(f, ["--config", tpl("team-ops"), "--dry-run"], env={}, settings=s)
    assert r.code == 0, r.all
    assert f.calls and all(c[0] == "GET" for c in f.calls)


@test
def environment_variables_win_over_the_settings_file():
    f = empty_server()
    s = os.path.join(TMP, "stale.env")
    with open(s, "w") as fh:
        fh.write("DISCORD_BOT_TOKEN=an-old-wrong-token\nDISCORD_GUILD_ID=333333333333333333\n")
    r = run(f, ["--config", tpl("team-ops"), "--dry-run"],
            env={"DISCORD_BOT_TOKEN": GOOD_TOKEN, "DISCORD_GUILD_ID": G}, settings=s)
    assert r.code == 0 and not f.foreign, r.all


@test
def missing_server_id_gives_clear_error_and_sends_nothing():
    f = empty_server()
    r = run(f, ["--config", tpl("team-ops"), "--dry-run"])
    assert r.code == 2 and "No server id found" in r.err and not f.calls


@test
def malformed_server_id_is_refused():
    f = empty_server()
    r = run(f, ["--config", tpl("team-ops"), "--guild", "CLIENT-HQ", "--dry-run"])
    assert r.code == 2 and "does not look right" in r.err and not f.calls


@test
def two_different_server_ids_are_refused():
    f = empty_server()
    p = layout({"name": "x", "guild_id": "222222222222222222", "categories": []})
    r = run(f, ["--config", p, "--guild", G])
    assert r.code == 2 and "Two different servers" in r.err and not f.calls


@test
def wrong_token_is_explained_and_changes_nothing():
    f = empty_server()
    r = run(f, ["--config", tpl("team-ops"), "--guild", G], env={"DISCORD_BOT_TOKEN": "wrong-token-value"})
    assert r.code == 2 and "rejected the bot token" in r.err and not f.writes()
    assert "wrong-token-value" not in r.all


@test
def bot_not_in_the_server_is_explained_and_changes_nothing():
    f = empty_server()
    r = run(f, ["--config", tpl("team-ops"), "--guild", "333333333333333333"])
    assert r.code == 2 and "cannot see server 333333333333333333" in r.err and not f.writes()


@test
def broken_layout_files_are_refused_before_any_request():
    f = empty_server()
    bad = os.path.join(TMP, "bad.json")
    with open(bad, "w") as fh:
        fh.write("{ not json")
    r = run(f, ["--config", bad, "--guild", G])
    assert r.code == 2 and "not valid JSON" in r.err
    for obj, expect in [
        ({"options": {"existing_channels": "nuke"}, "categories": []}, "must be keep, archive or replace"),
        ({"categories": [{"name": "A", "channels": [{"name": "x", "type": "txt"}]}]}, "is not one of"),
        ({"categories": [{"name": "A", "channels": [{"name": "x"}, {"name": "x"}]}]}, "appears twice"),
        ({"categories": [{"name": "A", "channels": [{"name": "c%d" % i} for i in range(51)]}]}, "50 per category"),
        ({"roles": [{"name": "R", "permissions": "lots"}], "categories": []}, "must be a number"),
        ({"categories": [], "community": {"rules_channel": "rules"}}, "not a channel in this layout"),
    ]:
        r = run(f, ["--config", layout(obj), "--guild", G])
        assert r.code == 2 and expect in r.err, (expect, r.err)
    r = run(f, ["--config", os.path.join(TMP, "nope.json"), "--guild", G])
    assert r.code == 2 and "Cannot read the layout file" in r.err
    assert not f.calls


@test
def no_arguments_explains_what_to_do():
    r = run(empty_server(), [])
    assert r.code == 2 and "Nothing to do" in r.err


# ------------------------------------------------------------ dry run

@test
def dry_run_never_writes_for_any_template_mode_or_server():
    for name in ("team-ops", "community", "coaching-client"):
        for mode in ("keep", "archive", "replace"):
            for make in (empty_server, lived_in_server, lambda: lived_in_server(community=True)):
                f = make()
                before = f.snapshot()
                r = run(f, ["--config", tpl(name), "--guild", G, "--existing", mode, "--dry-run",
                            "--confirm-replace", G])
                assert r.code == 0, r.all
                assert f.calls and all(c[0] == "GET" for c in f.calls), "dry run sent a write"
                assert f.snapshot() == before and not f.foreign
                assert "[dry-run] POST" in r.out and "DRY RUN COMPLETE. Nothing was changed." in r.out
                assert not r.sleeps


@test
def dry_run_shows_each_option_for_what_it_is():
    r = run(lived_in_server(), ["--config", tpl("team-ops"), "--guild", G, "--existing", "keep", "--dry-run"])
    assert "DELETE" not in r.out and "archived" not in r.out and "kept the 6 existing" in r.out
    r = run(lived_in_server(), ["--config", tpl("team-ops"), "--guild", G, "--existing", "archive", "--dry-run"])
    assert r.out.count("> archived") == 4 and r.out.count("removed old (now empty) category") == 2
    assert "- deleted" not in r.out
    r = run(lived_in_server(), ["--config", tpl("team-ops"), "--guild", G, "--existing", "replace", "--dry-run"])
    assert "PERMANENTLY DELETE 4 channel(s) and 2 categories" in r.out and r.out.count("- deleted") == 6


# ------------------------------------------------------------ building

@test
def build_into_empty_server_every_template_and_option():
    for name in ("team-ops", "community", "coaching-client"):
        cfg, names = wanted_names(tpl(name))
        for mode in ("keep", "archive", "replace"):
            f = empty_server()
            r = run(f, ["--config", tpl(name), "--guild", G, "--existing", mode, "--verify"])
            assert r.code == 0, r.all
            assert " 0 failed" in r.out and "FAIL  " not in r.out
            assert sorted(c["name"] for c in f.channels) == sorted(names), "channel list differs"
            assert {x["name"] for x in cfg["roles"]} <= {x["name"] for x in f.roles}
            assert not [c for c in f.writes() if c[0] == "DELETE" or c[1].startswith("/channels/")]
            assert_sane_order(f)
            last = f.writes()[-1]
            assert last[0] == "PATCH" and last[1].endswith("/channels") and len(last[2]) == len(names)


@test
def private_and_read_only_channels_get_the_right_permissions():
    f = empty_server()
    assert run(f, ["--config", tpl("team-ops"), "--guild", G]).code == 0
    role = {r["name"]: r["id"] for r in f.roles}
    lead = f.by_name("leadership-chat")[0]
    ow = {o["id"]: o for o in lead["permission_overwrites"]}
    assert ow[G]["deny"] == "1024" and ow[G]["type"] == 0
    assert {k for k in ow if k != G} == {role["Founder"], role["Manager"]}      # "Admin" role does not exist
    assert all(ow[k]["allow"] == "1024" for k in ow if k != G)
    ann = f.by_name("📢・announcements")[0]
    ow = {o["id"]: o for o in ann["permission_overwrites"]}
    assert ow[G]["deny"] == "2048" and ow[role["Founder"]]["allow"] == "2048"
    assert f.by_name("💬・general")[0]["permission_overwrites"] == []
    assert f.by_name("Team Call")[0]["type"] == 2
    founder = next(r for r in f.roles if r["name"] == "Founder")
    assert founder["permissions"] == "8" and founder["color"] == 0xF1C40F


@test
def keep_leaves_every_existing_channel_exactly_as_it_was():
    f = lived_in_server()
    before = {c["id"]: json.dumps(c, sort_keys=True) for c in f.channels}
    r = run(f, ["--config", tpl("team-ops"), "--guild", G, "--existing", "keep", "--verify"])
    assert r.code == 0 and " 0 failed" in r.out, r.all
    for cid, was in before.items():
        assert json.dumps(f.channel(cid), sort_keys=True) == was, "an existing channel was altered"
    assert not [c for c in f.writes() if c[0] == "DELETE" or c[1].startswith("/channels/")]
    assert_sane_order(f, before)


@test
def archive_moves_old_channels_into_a_hidden_category_and_deletes_nothing_with_messages():
    f = lived_in_server()
    old = {c["id"]: c["type"] for c in f.channels}
    r = run(f, ["--config", tpl("team-ops"), "--guild", G, "--existing", "archive", "--verify"])
    assert r.code == 0 and " 0 failed" in r.out, r.all
    arch = f.by_name("📦 Archive")
    assert len(arch) == 1 and arch[0]["permission_overwrites"][0] == {"id": G, "type": 0, "allow": "0", "deny": "1024"}
    for cid, t in old.items():
        if t == 4:
            assert f.channel(cid) is None, "old category should be removed once empty"
        else:
            c = f.channel(cid)
            assert c and c["parent_id"] == arch[0]["id"], "old channel not in the archive"
            assert c["permission_overwrites"][0]["deny"] == "1024"
    deleted = [c[1].split("/")[2] for c in f.writes() if c[0] == "DELETE"]
    assert deleted and all(old[d] == 4 for d in deleted), "archive deleted something that was not a category"
    assert_sane_order(f, old)


@test
def replace_refuses_without_confirmation_and_changes_nothing():
    for extra, why in (([], "no confirmation was given"), (["--confirm-replace", "222222222222222222"], "does not match"),
                       (["--confirm-replace", "yes"], "does not match")):
        f = lived_in_server()
        before = f.snapshot()
        r = run(f, ["--config", tpl("team-ops"), "--guild", G, "--existing", "replace"] + extra)
        assert r.code == 2 and "Replace was not confirmed" in r.err and why in r.err, r.all
        assert not f.writes() and f.snapshot() == before


@test
def replace_with_confirmation_deletes_only_what_was_there_before():
    f = lived_in_server()
    old = ids(f)
    cfg, names = wanted_names(tpl("team-ops"))
    r = run(f, ["--config", tpl("team-ops"), "--guild", G, "--existing", "replace",
                "--confirm-replace", G, "--verify"])
    assert r.code == 0 and " 0 failed" in r.out, r.all
    deleted = {c[1].split("/")[2] for c in f.writes() if c[0] == "DELETE"}
    assert deleted == old, "deleted set is not exactly the pre-existing channels"
    assert sorted(c["name"] for c in f.channels) == sorted(names)
    assert_sane_order(f, old)


@test
def replace_on_empty_server_needs_no_confirmation():
    f = empty_server()
    r = run(f, ["--config", tpl("team-ops"), "--guild", G, "--existing", "replace"])
    assert r.code == 0 and not [c for c in f.writes() if c[0] == "DELETE"], r.all


@test
def the_other_server_is_never_touched():
    f = lived_in_server()
    for mode in ("keep", "archive", "replace"):
        run(f, ["--config", tpl("community"), "--guild", G, "--existing", mode, "--confirm-replace", G])
    for method, path, body in f.calls:
        assert f.other_guild_id not in path and f.other_channel_id not in path
    assert not f.foreign


@test
def community_server_gets_new_rules_and_updates_channels_before_old_ones_move():
    for mode in ("archive", "replace"):
        f = lived_in_server(community=True)
        old = ids(f)
        r = run(f, ["--config", tpl("community"), "--guild", G, "--existing", mode,
                    "--confirm-replace", G, "--verify"])
        assert r.code == 0 and " 0 failed" in r.out, r.all
        assert f.rules_channel_id == f.by_name("✅・rules")[0]["id"]
        assert f.public_updates_channel_id == f.by_name("🚨・announcements")[0]["id"]
        guild_patch = [c for c in f.writes() if c[0] == "PATCH" and c[1] == "/guilds/" + G][0]
        assert guild_patch[2]["features"] == ["COMMUNITY", "NEWS"] and guild_patch[2]["verification_level"] == 1
        assert_sane_order(f, old)


@test
def community_block_is_skipped_on_a_server_that_is_not_community():
    f = empty_server()
    r = run(f, ["--config", tpl("community"), "--guild", G])
    assert r.code == 0 and "not a Community server" in r.out
    assert not [c for c in f.writes() if c[0] == "PATCH" and c[1] == "/guilds/" + G]


@test
def replace_reports_channels_discord_will_not_delete():
    f = lived_in_server(community=True)          # team-ops has no rules channel to hand over to
    r = run(f, ["--config", tpl("team-ops"), "--guild", G, "--existing", "replace", "--confirm-replace", G])
    assert r.code == 1 and "FINISHED WITH 2 PROBLEM(S)" in r.out, r.all
    assert "could not delete rules" in r.out and f.channel(f.rules_channel_id)


@test
def running_the_build_twice_does_not_duplicate_or_archive_its_own_work():
    for mode in ("keep", "archive"):
        f = lived_in_server()
        assert run(f, ["--config", tpl("coaching-client"), "--guild", G, "--existing", mode]).code == 0
        before, n = f.snapshot(), len(f.calls)
        r = run(f, ["--config", tpl("coaching-client"), "--guild", G, "--existing", mode, "--verify"])
        second = [c for c in f.calls[n:] if c[0] != "GET"]
        assert r.code == 0 and " 0 failed" in r.out, r.all
        assert [c for c in second if c[0] != "PATCH" or not c[1].endswith("/guilds/%s/channels" % G)] == [], second
        assert json.loads(f.snapshot())[0] == json.loads(before)[0], "second run changed the channels"


@test
def archive_of_more_than_fifty_channels_spills_into_a_second_category():
    f = empty_server()
    for i in range(60):
        f.add_channel("old-%d" % i)
    r = run(f, ["--config", tpl("team-ops"), "--guild", G, "--existing", "archive", "--verify"])
    assert r.code == 0 and " 0 failed" in r.out, r.all
    a1, a2 = f.by_name("📦 Archive")[0], f.by_name("📦 Archive 2")[0]
    assert len([c for c in f.channels if c["parent_id"] == a1["id"]]) == 50
    assert len([c for c in f.channels if c["parent_id"] == a2["id"]]) == 10


# ------------------------------------------------------------ permissions

@test
def bot_without_enough_permission_is_stopped_before_any_change():
    f = lived_in_server(bot_permissions=BUILDER_PERMS)
    r = run(f, ["--config", tpl("team-ops"), "--guild", G, "--existing", "archive"])
    assert r.code == 2 and "missing permissions" in r.err and "ADMINISTRATOR" in r.err, r.all
    assert "permissions.py" in r.err and "Nothing in the server was changed" in r.err and not f.writes()
    r = run(f, ["--config", tpl("team-ops"), "--guild", G, "--dry-run"])
    assert r.code == 0 and "WARNING: the real build would stop" in r.out and not f.writes()
    f = empty_server(bot_permissions=0)
    r = run(f, ["--config", layout({"categories": [{"name": "A", "channels": [{"name": "x"}]}]}), "--guild", G])
    assert r.code == 2 and "MANAGE_CHANNELS" in r.err and not f.writes()


@test
def least_privilege_bot_builds_a_layout_with_no_admin_roles():
    p = layout({"name": "small", "options": {"existing_channels": "archive", "staff_roles": ["Lead"]},
                "roles": [{"name": "Lead", "colour": "#3498DB", "permissions": "0"}],
                "uncategorised": [{"name": "rules", "readonly": True}],
                "categories": [{"name": "Staff", "private": True, "channels": [{"name": "staff-chat"}]},
                               {"name": "Open", "channels": [{"name": "general"}, {"name": "Call", "type": "voice"}]}]})
    f = lived_in_server(bot_permissions=BUILDER_PERMS)
    r = run(f, ["--config", p, "--guild", G, "--verify"])
    assert r.code == 0 and " 0 failed" in r.out, r.all
    ow = f.by_name("staff-chat")[0]["permission_overwrites"]
    assert {"id": BOT_ID, "type": 1, "allow": "1024", "deny": "0"} in ow, "bot did not keep sight of a hidden channel"


@test
def a_403_in_the_middle_stops_cleanly_and_a_rerun_finishes_without_duplicates():
    f = lived_in_server()
    f.inject("POST", r"/channels$", 403, {"message": "Missing Permissions", "code": 50013}, times=0)
    # let roles and the first five channel creations through, then refuse one
    real = f.urlopen
    state = {"n": 0}

    def flaky(req, *a, **k):
        if req.get_method() == "POST" and req.full_url.endswith("/channels"):
            state["n"] += 1
            if state["n"] == 6:
                f.injected[0]["times"] = 1
        return real(req, *a, **k)
    f.urlopen = flaky
    r = run(f, ["--config", tpl("team-ops"), "--guild", G, "--existing", "archive"])
    assert r.code == 1 and "STOPPED" in r.err and "missing a permission" in r.err, r.all
    assert "run the same" in r.err and len(f.by_name("general")) == 1
    assert not [c for c in f.writes() if c[0] == "DELETE" or c[1].startswith("/channels/")], "old channels touched"
    f.urlopen = real
    r = run(f, ["--config", tpl("team-ops"), "--guild", G, "--existing", "archive", "--verify"])
    assert r.code == 0 and " 0 failed" in r.out, r.all
    cfg, names = wanted_names(tpl("team-ops"))
    live = [c["name"] for c in f.channels]
    for n in names:
        assert live.count(n) == 1, "%s appears %d times" % (n, live.count(n))


@test
def a_403_while_archiving_is_reported_and_the_rest_carries_on():
    f = lived_in_server()
    f.inject("PATCH", r"^/channels/\d+$", 403, {"message": "Missing Access", "code": 50001}, times=1)
    r = run(f, ["--config", tpl("team-ops"), "--guild", G, "--existing", "archive"])
    assert r.code == 1 and "FINISHED WITH 1 PROBLEM(S)" in r.out and "could not archive general" in r.out, r.all
    assert r.out.count("> archived") == 3 and "left category Text Channels in place" in r.out
    assert f.by_name("Text Channels") and not f.by_name("Voice Channels")


# ------------------------------------------------------------ rate limits and outages

@test
def a_429_is_waited_out_and_the_same_request_retried():
    f = empty_server()
    f.inject("POST", r"/channels$", 429, {"message": "You are being rate limited.", "retry_after": 0.7,
                                         "global": False}, times=2)
    r = run(f, ["--config", tpl("team-ops"), "--guild", G, "--verify"])
    assert r.code == 0 and " 0 failed" in r.out, r.all
    assert r.sleeps.count(1.2) == 2, r.sleeps
    posts = [c for c in f.calls if c[0] == "POST" and c[1].endswith("/channels")]
    assert posts[0] == posts[1] == posts[2] and len(f.by_name("📋 MAIN")) == 1
    assert all(s in (0.4, 1.2) for s in r.sleeps) and r.sleeps.count(0.4) == len(f.writes()) - 2


@test
def a_429_without_json_uses_the_retry_after_header():
    f = empty_server()
    f.inject("POST", r"/roles$", 429, raw="<html>rate limited</html>", headers={"Retry-After": "3"})
    r = run(f, ["--config", tpl("team-ops"), "--guild", G])
    assert r.code == 0 and 3.5 in r.sleeps, (r.all, r.sleeps)


@test
def endless_429s_stop_with_a_clear_message():
    f = empty_server()
    f.inject("POST", r"/roles$", 429, {"message": "rate limited", "retry_after": 1}, times=99)
    r = run(f, ["--config", tpl("team-ops"), "--guild", G])
    assert r.code == 2 and "rate limiting" in r.err and not f.roles[2:], r.all
    f = empty_server()
    f.inject("POST", r"/roles$", 429, {"message": "rate limited", "retry_after": 3600})
    r = run(f, ["--config", tpl("team-ops"), "--guild", G])
    assert r.code == 2 and "rate limiting" in r.err and 3600.5 not in r.sleeps, r.all


@test
def a_discord_server_error_is_retried():
    f = empty_server()
    f.inject("POST", r"/channels$", 502, raw="Bad Gateway", times=2)
    r = run(f, ["--config", tpl("team-ops"), "--guild", G, "--verify"])
    assert r.code == 0 and " 0 failed" in r.out, r.all


@test
def no_internet_gives_a_clear_message():
    f = empty_server()
    f.network_down = True
    r = run(f, ["--config", tpl("team-ops"), "--guild", G])
    assert r.code == 1 and "Could not reach Discord" in r.err, r.all


# ------------------------------------------------------------ verify and check

@test
def verify_alone_and_check_only_read():
    f = lived_in_server()
    r = run(f, ["--guild", G, "--verify"])
    assert r.code == 0 and "=== Acme HQ" in r.out and "total channels: 6" in r.out
    r = run(f, ["--config", tpl("team-ops"), "--guild", G, "--check"])
    assert r.code == 1 and "FAIL  category 📋 MAIN exists" in r.out
    assert all(c[0] == "GET" for c in f.calls)


@test
def check_catches_a_channel_that_lost_its_lock():
    f = empty_server()
    assert run(f, ["--config", tpl("team-ops"), "--guild", G]).code == 0
    f.by_name("ops-admin")[0]["permission_overwrites"] = []
    f.channels.remove(f.by_name("payments")[0])
    r = run(f, ["--config", tpl("team-ops"), "--guild", G, "--check"])
    assert r.code == 1 and "FAIL  channel ops-admin is hidden from @everyone" in r.out
    assert "FAIL  channel payments exists in 💳 PAYMENTS" in r.out and " 2 failed" in r.out


# ------------------------------------------------------------ invite link helper

@test
def permissions_helper_prints_the_right_numbers():
    r = run(None, [], script="permissions.py")
    assert "permissions: 268438576" in r.out
    r = run(None, ["--config", tpl("team-ops"), "--client-id", "123456789012345678"], script="permissions.py")
    assert "permissions: 8\n" in r.out and "client_id=123456789012345678&scope=bot&permissions=8&" in r.out
    p = layout({"roles": [{"name": "Mod", "permissions": "1099511627846"}], "categories": []})
    r = run(None, ["--config", p], script="permissions.py")
    assert "permissions: %d" % (268438576 | 1099511627846) in r.out and "MODERATE_MEMBERS" in r.out
    r = run(None, ["--extra", "NOT_A_PERMISSION"], script="permissions.py")
    assert r.code == 1 and "Unknown permission" in r.err


def main():
    print("Offline tests for discord-server-builder (no network, made-up token and server)")
    print("scripts under test: %s\n" % SCRIPTS)
    failed = 0
    try:
        for t in TESTS:
            try:
                t()
                print("PASS  " + t.__name__.replace("_", " "))
            except AssertionError as e:
                failed += 1
                print("FAIL  " + t.__name__.replace("_", " "))
                print("        " + str(e)[:1500].replace("\n", "\n        "))
            except Exception as e:      # a crash in the script under test is a failure too
                failed += 1
                print("FAIL  %s (crashed: %s: %s)" % (t.__name__.replace("_", " "), type(e).__name__, e))
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print("\n%d passed, %d failed, %d tests" % (len(TESTS) - failed, failed, len(TESTS)))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
