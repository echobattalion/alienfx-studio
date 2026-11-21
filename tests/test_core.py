"""Run with: python3 -m unittest discover -s tests

Uses a fake CLI and temporary directories, so it never touches real lights.
"""
import json
import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TMP = tempfile.TemporaryDirectory()
LOG = Path(TMP.name) / "cli.log"
FAKE = Path(TMP.name) / "fake_cli"
FAKE.write_text(f"""#!{sys.executable}
import json, sys
args = sys.argv[1:]
stdin = sys.stdin.read() if "setbatch" in args else ""
with open({str(LOG)!r}, "a") as f:
    f.write(json.dumps({{"args": args, "stdin": stdin}}) + "\\n")
""")
FAKE.chmod(FAKE.stat().st_mode | stat.S_IEXEC)
os.environ.update(ALIENFX_CLI=str(FAKE), XDG_RUNTIME_DIR=TMP.name, XDG_CONFIG_HOME=TMP.name,
                  QT_QPA_PLATFORM="offscreen")
os.environ.pop("ALIENFX_KEYBOARD_DEV", None)
os.environ.pop("ALIENFX_ELC_DEV", None)

from alienfx_studio import device, kbmap  # noqa: E402
from alienfx_studio.profiles import Profile, ProfileError, Store, default_profile  # noqa: E402


def calls():
    return [json.loads(line) for line in LOG.read_text().splitlines()] if LOG.exists() else []


def counts(entries):
    lines = [l for e in entries for l in e["stdin"].splitlines()]
    return (sum(l.startswith("0 ") for l in lines), sum(l.startswith("1 ") for l in lines),
            sum("setpower" in e["args"] for e in entries))


class ProfileTests(unittest.TestCase):
    def test_round_trip(self):
        p = default_profile()
        self.assertEqual(Profile.from_dict(p.to_dict()).to_dict(), p.to_dict())

    def test_rejects_bad_color(self):
        d = default_profile().to_dict()
        d["keyboard"]["default"] = "red"
        with self.assertRaises(ProfileError):
            Profile.from_dict(d)

    def test_store_rename_and_last_profile(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(root)
            store.rename("Default", "Mine")
            self.assertEqual(store.active_name(), "Mine")
            with self.assertRaises(ProfileError):
                store.delete("Mine")


class DeviceTests(unittest.TestCase):
    def setUp(self):
        LOG.unlink(missing_ok=True)

    def test_full_frame(self):
        device.send(device.build_frame(default_profile()))
        entries = calls()
        self.assertEqual(counts(entries), (kbmap.N, 19, 1))
        self.assertNotIn("--save", [a for e in entries for a in e["args"]])
        self.assertEqual(entries[1]["args"][3:5], ["1", "19"])

    def test_arrows_write_every_bank(self):
        lines = device.build_frame(default_profile()).batch.splitlines()
        for name in ("LEFT", "DOWN", "RIGHT"):
            for i in kbmap.KEYS[name]:
                self.assertIn(f"0 {i} 0 0 255", lines)

    def test_delta(self):
        p = default_profile()
        base = device.build_frame(p)
        p.keys["W"] = "#00FF00"
        d = device.delta(device.build_frame(p), base)
        self.assertEqual((d.batch.count("\n"), len(d.power)), (kbmap.N, 0))
        p = default_profile()
        p.zones[12] = "#010203"
        d = device.delta(device.build_frame(p), base)
        self.assertEqual((d.batch, d.power), ("1 12 1 2 3\n", ()))
        p = default_profile()
        p.zones[19] = "#010203"
        d = device.delta(device.build_frame(p), base)
        self.assertEqual((d.batch, d.power), ("", ((19, (1, 2, 3)),)))

    def test_external_write_forces_full_resync(self):
        done = []
        writer = device.LiveWriter(lambda ok, msg: done.append(ok))
        p = default_profile()
        writer.submit(device.build_frame(p))
        while not done:
            time.sleep(.005)
        time.sleep(.01)
        device.send(device.build_frame(default_profile()))
        LOG.unlink()
        p.zones[12] = "#040506"
        writer.submit(device.build_frame(p))
        while len(done) < 2:
            time.sleep(.005)
        writer.close()
        self.assertEqual(counts(calls()), (kbmap.N, 19, 1))


class GuiTests(unittest.TestCase):
    def test_edit_undo_save(self):
        try:
            from PyQt6.QtGui import QColor
            from PyQt6.QtWidgets import QApplication
        except ImportError:
            self.skipTest("PyQt6 not installed")
        from alienfx_studio.app import MainWindow
        app = QApplication.instance() or QApplication([])
        win = MainWindow()
        original = win.profile.color_of("K:W")
        win.view.select({"K:W"})
        self.assertFalse(win.dirty)
        win.picker.setCurrentColor(QColor("#00FFFF"))
        app.processEvents()
        self.assertEqual(win.profile.color_of("K:W"), "#00FFFF")
        self.assertTrue(win.dirty)
        win.undo()
        self.assertEqual(win.profile.color_of("K:W"), original)
        self.assertFalse(win.dirty)
        win.writer.close()


if __name__ == "__main__":
    unittest.main()
