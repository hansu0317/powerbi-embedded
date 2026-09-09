import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from services import backup


class BackupTests(unittest.TestCase):
    def test_failed_dump_leaves_no_completed_backup(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(backup, "BACKUP_DIR", Path(directory)):
            def fail(cmd, **kw):
                Path(cmd[cmd.index("-f")+1]).write_bytes(b"")
                raise subprocess.CalledProcessError(1, ["pg_dump"])
            with patch.object(backup.subprocess, "run", side_effect=fail):
                with self.assertRaises(subprocess.CalledProcessError):
                    backup._run_pg_dump()
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_empty_dump_is_not_success(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(backup, "BACKUP_DIR", Path(directory)):
            with patch.object(backup.subprocess, "run"):
                with self.assertRaises(RuntimeError):
                    backup._run_pg_dump()

    def test_success_publishes_complete_file(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(backup, "BACKUP_DIR", Path(directory)):
            def success(cmd, **kw):
                Path(cmd[cmd.index("-f")+1]).write_bytes(b"PGDMP-test")
            with patch.object(backup.subprocess, "run", side_effect=success):
                result = backup._run_pg_dump()
            self.assertEqual(result.read_bytes(), b"PGDMP-test")
            self.assertEqual(result.suffix, ".dump")

    def test_retention_preserves_manual_backups(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(backup, "BACKUP_DIR", Path(directory)):
            old = time.time() - 86400 * 60
            for name in ("backup_20260101_170000.dump", "backup_before_change.dump"):
                path = Path(directory) / name
                path.write_bytes(b"test")
                os.utime(path, (old, old))
            self.assertEqual(backup._cleanup_old_backups(), 1)
            self.assertTrue((Path(directory) / "backup_before_change.dump").exists())
