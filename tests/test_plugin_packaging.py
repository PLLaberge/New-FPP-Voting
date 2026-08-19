"""
The FPP plugin packaging, checked statically.

None of this can be tested against real FPP without a Pi — see docs/DEPLOY.md.
What's worth catching here is the class of mistake that would only surface
once someone actually tried to install it: a typo'd field FPP silently
ignores, a script with a syntax error, a systemd template missing a
placeholder fpp_install.sh actually fills in, a menu link to a route the
service doesn't have.
"""
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_LIFECYCLE_SCRIPTS = [
    "fpp_install.sh", "fpp_uninstall.sh",
    "preStart.sh", "postStart.sh", "preStop.sh", "postStop.sh",
]


# ---------------------------------------------------------------- pluginInfo
def test_plugin_info_is_valid_json_with_the_fields_fpp_requires():
    info = json.loads((ROOT / "pluginInfo.json").read_text())
    for field in ("repoName", "name", "author", "description", "homeURL",
                  "srcURL", "bugURL", "versions"):
        assert field in info, f"pluginInfo.json is missing {field!r}"
    assert info["srcURL"].endswith(".git")
    assert info["srcURL"].startswith("https://")
    assert info["versions"], "at least one FPP version compatibility entry is required"
    for v in info["versions"]:
        for field in ("minFPPVersion", "maxFPPVersion", "branch"):
            assert field in v


def test_plugin_info_version_bands_do_not_leave_a_gap():
    """maxFPPVersion "0" means unbounded WITHIN that entry's own major
    version, not "and everything after" -- confirmed against the real FPP UI,
    which showed a single {min: "8.0", max: "0"} entry as covering only
    v8.0-v8.999. Covering FPP 9+ (and beyond) needs its own band per major
    version, the same way fpp-plugin-Template's own example does it. Assert
    there is no gap between consecutive minor-version ceilings and majors,
    so a future edit can't reintroduce a version FPP silently refuses.
    """
    info = json.loads((ROOT / "pluginInfo.json").read_text())
    bands = sorted(info["versions"], key=lambda v: float(v["minFPPVersion"]))
    assert float(bands[0]["minFPPVersion"]) <= 8.0, \
        "must cover from FPP 8.0, the earliest version this project supports"
    assert bands[-1]["maxFPPVersion"] == "0", \
        "the last band must be open-ended, or a future FPP major is silently unsupported"
    for prev, nxt in zip(bands, bands[1:]):
        # "8.999" -> next band's min must be "9.0", not "9.1" (a gap) or "8.5"
        # (an overlap hiding a typo).
        assert int(float(prev["maxFPPVersion"])) + 1 == int(float(nxt["minFPPVersion"])), \
            f"gap or overlap between {prev} and {nxt}"


def test_plugin_info_does_not_declare_python_deps_installed_system_wide():
    """FPP 10+ installs pluginInfo.json's dependencies.python with
    `pip install --break-system-packages` against the SYSTEM python — exactly
    what CLAUDE.md's 'ship a venv inside the plugin folder' decision exists to
    avoid. fpp_install.sh does its own venv install instead; this field must
    stay empty so a future edit doesn't silently reintroduce the system-wide
    path.
    """
    info = json.loads((ROOT / "pluginInfo.json").read_text())
    assert "python" not in info.get("dependencies", {})


def test_plugin_info_declares_the_venv_module_as_a_system_dependency():
    """python3 -m venv fails outright on a Debian/Ubuntu image that never
    installed python3-venv. Declaring it here means FPP installs it before
    fpp_install.sh runs, instead of fpp_install.sh failing on a fresh Pi."""
    info = json.loads((ROOT / "pluginInfo.json").read_text())
    assert "python3-venv" in info.get("dependencies", {}).get("packages", [])


# ------------------------------------------------------------------ scripts
def test_every_lifecycle_script_exists_and_is_executable():
    for name in REQUIRED_LIFECYCLE_SCRIPTS:
        path = ROOT / "scripts" / name
        assert path.is_file(), f"missing scripts/{name}"
        assert path.stat().st_mode & 0o111, f"scripts/{name} is not executable"


def test_every_lifecycle_script_is_valid_bash():
    """bash, not sh: a real install against FPP 9.3 failed with dash choking on
    FPP's own ${FPPDIR}/scripts/common, which is bash-only. See the comment in
    fpp_install.sh. `sh -n` would pass that broken shebang right through —
    checking with bash is what would have caught it."""
    for name in REQUIRED_LIFECYCLE_SCRIPTS:
        path = ROOT / "scripts" / name
        assert path.read_text().startswith("#!/bin/bash"), \
            f"scripts/{name} must be #!/bin/bash, not #!/bin/sh"
        result = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
        assert result.returncode == 0, f"scripts/{name}: {result.stderr}"


def test_install_and_uninstall_reference_files_that_actually_exist():
    """A typo'd path here fails silently on the Pi with no test to catch it
    first — sourced files and scripts fpp_install.sh calls must exist."""
    install = (ROOT / "scripts" / "fpp_install.sh").read_text()
    assert (ROOT / "requirements.txt").is_file()
    assert "requirements.txt" in install
    assert (ROOT / "tools" / "init_db.py").is_file()
    assert "tools/init_db.py" in install
    assert (ROOT / "deploy" / "fppvote.service.template").is_file()
    assert "fppvote.service.template" in install


def test_fpp_start_stop_hooks_do_not_couple_the_service_to_fppds_lifecycle():
    """Deliberate: this plugin exists to survive FPP being unreachable, so its
    own service must not stop every time fppd does. See the comment in
    scripts/preStart.sh. A `systemctl stop`/`start fppvote` creeping into
    these would silently undo that."""
    for name in ("preStart.sh", "postStop.sh", "preStop.sh"):
        text = (ROOT / "scripts" / name).read_text()
        assert "systemctl stop fppvote" not in text
        assert "systemctl start fppvote" not in text


# ------------------------------------------------------------------- systemd
def test_the_systemd_template_has_every_placeholder_fpp_install_fills_in():
    template = (ROOT / "deploy" / "fppvote.service.template").read_text()
    install = (ROOT / "scripts" / "fpp_install.sh").read_text()
    for placeholder in ("__PLUGIN_DIR__", "__DB_PATH__", "__FPP_USER__",
                        "__FPP_GROUP__", "__PORT__"):
        assert placeholder in template, f"template never uses {placeholder}"
        assert placeholder in install, f"fpp_install.sh never substitutes {placeholder}"


def test_the_systemd_unit_restarts_on_failure():
    template = (ROOT / "deploy" / "fppvote.service.template").read_text()
    assert "Restart=on-failure" in template


# ---------------------------------------------------------------------- menu
def test_menu_entries_point_at_routes_the_service_actually_serves():
    menu = (ROOT / "menu.inc").read_text()
    assert '$base/"' in menu or "$base/'" in menu \
        or '"$base/"' in menu, "no link to the voter page"
    assert "$base/admin" in menu, "no link to the admin page"


def test_menu_has_at_most_one_entry_per_type():
    """PLUGIN_GUIDELINES.md #9.1 — a second entry of the same type silently
    overwrites the first in FPP's menu rendering rather than erroring."""
    menu = (ROOT / "menu.inc").read_text()
    array_body = menu[menu.index("$menuEntries = Array("):menu.index("foreach")]
    # Only count active (uncommented) entries — a `#`-prefixed example row is
    # not a real second entry of that type.
    active_lines = [l for l in array_body.splitlines() if not l.strip().startswith("#")]
    active = "\n".join(active_lines)
    types = [t for t in ("status", "content", "output", "help")
             if active.count(f"'type' => '{t}'") > 1]
    assert not types, f"more than one menu entry for type(s): {types}"
