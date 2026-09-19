"""Unit and regression tests for grok_pipeline CLI and logic."""

import argparse
import glob
import json
import os
import sqlite3
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

# Ensure project root is importable
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import grok_pipeline


class TestBuildParser(unittest.TestCase):
    """Verify CLI argument parser structure and defaults."""

    def test_parser_returns_argparse_instance(self):
        parser = grok_pipeline.build_parser()
        self.assertIsInstance(parser, argparse.ArgumentParser)

    def test_register_defaults(self):
        parser = grok_pipeline.build_parser()
        args = parser.parse_args(["register"])
        self.assertEqual(args.command, "register")
        self.assertEqual(args.count, 1)
        self.assertFalse(args.mint)
        self.assertFalse(args.push_9router)
        self.assertEqual(args.sleep, 6.0)

    def test_register_all_flags(self):
        parser = grok_pipeline.build_parser()
        args = parser.parse_args([
            "register", "--count", "5", "--mint", "--push-9router", "--sleep", "3.5",
        ])
        self.assertEqual(args.count, 5)
        self.assertTrue(args.mint)
        self.assertTrue(args.push_9router)
        self.assertAlmostEqual(args.sleep, 3.5)

    def test_mint_defaults(self):
        parser = grok_pipeline.build_parser()
        args = parser.parse_args(["mint"])
        self.assertEqual(args.command, "mint")
        self.assertIsNone(args.limit)
        self.assertEqual(args.sleep, 8.0)

    def test_mint_with_options(self):
        parser = grok_pipeline.build_parser()
        args = parser.parse_args(["mint", "--limit", "10", "--sleep", "2"])
        self.assertEqual(args.limit, 10)
        self.assertAlmostEqual(args.sleep, 2.0)

    def test_audit_subcommand(self):
        parser = grok_pipeline.build_parser()
        args = parser.parse_args(["audit"])
        self.assertEqual(args.command, "audit")

    def test_verbose_flag(self):
        parser = grok_pipeline.build_parser()
        args = parser.parse_args(["-v", "audit"])
        self.assertTrue(args.verbose)

    def test_no_command_returns_none(self):
        parser = grok_pipeline.build_parser()
        args = parser.parse_args([])
        self.assertIsNone(args.command)


class TestParseAccountsFile(unittest.TestCase):
    """Verify accounts.txt parsing logic."""

    def test_parse_standard_format(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as fh:
            fh.write("user@test.com----pass123----ssotoken\n")
            fh.write("bob@test.com----secret----abc\n")
            path = fh.name
        try:
            accounts = grok_pipeline._parse_accounts_file(path)
            self.assertEqual(len(accounts), 2)
            self.assertEqual(accounts[0]["email"], "user@test.com")
            self.assertEqual(accounts[0]["password"], "pass123")
            self.assertEqual(accounts[0]["sso"], "ssotoken")
            self.assertEqual(accounts[1]["email"], "bob@test.com")
        finally:
            os.unlink(path)

    def test_parse_indexed_format(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as fh:
            fh.write("1|user@test.com----pass123----ssotoken\n")
            path = fh.name
        try:
            accounts = grok_pipeline._parse_accounts_file(path)
            self.assertEqual(len(accounts), 1)
            self.assertEqual(accounts[0]["email"], "user@test.com")
        finally:
            os.unlink(path)

    def test_skip_blank_lines(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as fh:
            fh.write("\n\nuser@test.com----pass----sso\n\n")
            path = fh.name
        try:
            accounts = grok_pipeline._parse_accounts_file(path)
            self.assertEqual(len(accounts), 1)
        finally:
            os.unlink(path)

    def test_skip_invalid_lines(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as fh:
            fh.write("not-an-email----pass----sso\n")
            fh.write("----pass----sso\n")
            fh.write("valid@email.com----pass----sso\n")
            path = fh.name
        try:
            accounts = grok_pipeline._parse_accounts_file(path)
            self.assertEqual(len(accounts), 1)
            self.assertEqual(accounts[0]["email"], "valid@email.com")
        finally:
            os.unlink(path)

    def test_missing_sso_defaults_empty(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as fh:
            fh.write("user@test.com----pass123\n")
            path = fh.name
        try:
            accounts = grok_pipeline._parse_accounts_file(path)
            self.assertEqual(len(accounts), 1)
            self.assertEqual(accounts[0]["sso"], "")
        finally:
            os.unlink(path)

    def test_nonexistent_file_returns_empty(self):
        accounts = grok_pipeline._parse_accounts_file("/nonexistent/path.txt")
        self.assertEqual(accounts, [])


class TestGetMintedEmails(unittest.TestCase):
    """Verify _get_minted_emails aggregation from CPA files and DB."""

    def test_reads_cpa_json_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            auth_dir = os.path.join(tmpdir, "cpa_auths")
            os.makedirs(auth_dir)
            auth_file = os.path.join(auth_dir, "xai-minted@test.com.json")
            with open(auth_file, "w") as fh:
                json.dump({"email": "minted@test.com"}, fh)

            with mock.patch.object(grok_pipeline, "_PROJECT_ROOT", Path(tmpdir)):
                with mock.patch.object(grok_pipeline, "DB_PATH", "/nonexistent/db"):
                    emails = grok_pipeline._get_minted_emails()
            self.assertIn("minted@test.com", emails)

    def test_reads_9router_db(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "data.sqlite")
            conn = sqlite3.connect(db_path)
            conn.execute(
                """CREATE TABLE providerConnections (
                    id TEXT, provider TEXT, authType TEXT, name TEXT,
                    email TEXT, priority INT, isActive INT, data TEXT,
                    createdAt TEXT, updatedAt TEXT
                )"""
            )
            conn.execute(
                """INSERT INTO providerConnections VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "uuid1", "grok-cli", "oauth", "db@test.com",
                    "db@test.com", 1, 1, json.dumps({"email": "db@test.com"}),
                    "2026-01-01", "2026-01-01",
                ),
            )
            conn.commit()
            conn.close()

            # No cpa_auths dir
            auth_dir = os.path.join(tmpdir, "cpa_auths")
            os.makedirs(auth_dir)

            with mock.patch.object(grok_pipeline, "_PROJECT_ROOT", Path(tmpdir)):
                with mock.patch.object(grok_pipeline, "DB_PATH", db_path):
                    emails = grok_pipeline._get_minted_emails()
            self.assertIn("db@test.com", emails)


class TestGetFailedEmails(unittest.TestCase):
    """Verify _get_failed_emails reads failure log."""

    def test_reads_failed_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fail_dir = os.path.join(tmpdir, "cpa_auths")
            os.makedirs(fail_dir)
            fail_file = os.path.join(fail_dir, "cpa_auth_failed.txt")
            with open(fail_file, "w") as fh:
                fh.write("fail@test.com----some error----1700000000\n")

            with mock.patch.object(grok_pipeline, "_PROJECT_ROOT", Path(tmpdir)):
                failed = grok_pipeline._get_failed_emails()
            self.assertIn("fail@test.com", failed)

    def test_missing_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.object(grok_pipeline, "_PROJECT_ROOT", Path(tmpdir)):
                failed = grok_pipeline._get_failed_emails()
            self.assertEqual(failed, set())


class TestCmdAudit(unittest.TestCase):
    """Verify audit subcommand generates correct summary."""

    def test_audit_with_test_data(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create accounts file
            accounts_file = os.path.join(tmpdir, "accounts.txt")
            with open(accounts_file, "w") as fh:
                fh.write("minted@test.com----pass1----sso1\n")
                fh.write("unminted@test.com----pass2----sso2\n")
                fh.write("failed@test.com----pass3----sso3\n")

            # Create CPA auth for minted account
            auth_dir = os.path.join(tmpdir, "cpa_auths")
            os.makedirs(auth_dir)
            auth_file = os.path.join(auth_dir, "xai-minted@test.com.json")
            with open(auth_file, "w") as fh:
                json.dump({"email": "minted@test.com", "access_token": "tok"}, fh)

            # Create failure record
            fail_file = os.path.join(auth_dir, "cpa_auth_failed.txt")
            with open(fail_file, "w") as fh:
                fh.write("failed@test.com----timeout----1700000000\n")

            parser = grok_pipeline.build_parser()
            args = parser.parse_args(["audit"])

            with mock.patch.object(grok_pipeline, "_PROJECT_ROOT", Path(tmpdir)):
                with mock.patch.object(grok_pipeline, "DB_PATH", "/nonexistent/db"):
                    with mock.patch.object(
                        grok_pipeline, "_find_accounts_file", return_value=accounts_file
                    ):
                        result = grok_pipeline.cmd_audit(args)

            self.assertEqual(result["total_accounts"], 3)
            self.assertEqual(result["minted_count"], 1)
            self.assertEqual(result["unminted_count"], 1)
            self.assertEqual(result["failed_count"], 1)
            self.assertEqual(result["cpa_files"], 1)
            self.assertFalse(result["db_exists"])

    def test_audit_no_accounts_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "cpa_auths"))
            parser = grok_pipeline.build_parser()
            args = parser.parse_args(["audit"])

            with mock.patch.object(grok_pipeline, "_PROJECT_ROOT", Path(tmpdir)):
                with mock.patch.object(grok_pipeline, "DB_PATH", "/nonexistent/db"):
                    with mock.patch.object(
                        grok_pipeline, "_find_accounts_file", return_value=""
                    ):
                        result = grok_pipeline.cmd_audit(args)

            self.assertEqual(result["total_accounts"], 0)
            self.assertEqual(result["unminted_count"], 0)

    def test_audit_with_9router_db(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create accounts file
            accounts_file = os.path.join(tmpdir, "accounts.txt")
            with open(accounts_file, "w") as fh:
                fh.write("inrouter@test.com----pass1----sso1\n")

            # Create DB
            db_path = os.path.join(tmpdir, "data.sqlite")
            conn = sqlite3.connect(db_path)
            conn.execute(
                """CREATE TABLE providerConnections (
                    id TEXT, provider TEXT, authType TEXT, name TEXT,
                    email TEXT, priority INT, isActive INT, data TEXT,
                    createdAt TEXT, updatedAt TEXT
                )"""
            )
            conn.execute(
                """INSERT INTO providerConnections VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "uuid1", "grok-cli", "oauth", "inrouter@test.com",
                    "inrouter@test.com", 1, 1,
                    json.dumps({"email": "inrouter@test.com"}),
                    "2026-01-01", "2026-01-01",
                ),
            )
            conn.commit()
            conn.close()

            os.makedirs(os.path.join(tmpdir, "cpa_auths"))

            parser = grok_pipeline.build_parser()
            args = parser.parse_args(["audit"])

            with mock.patch.object(grok_pipeline, "_PROJECT_ROOT", Path(tmpdir)):
                with mock.patch.object(grok_pipeline, "DB_PATH", db_path):
                    with mock.patch.object(
                        grok_pipeline, "_find_accounts_file", return_value=accounts_file
                    ):
                        result = grok_pipeline.cmd_audit(args)

            self.assertTrue(result["db_exists"])
            self.assertEqual(result["db_grok_cli_total"], 1)
            self.assertEqual(result["db_from_accounts"], 1)


class TestFindAccountsFile(unittest.TestCase):
    """Verify accounts file discovery logic."""

    def test_finds_accounts_txt(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "accounts.txt")
            with open(path, "w") as fh:
                fh.write("x@y.com----p----s\n")
            with mock.patch.object(grok_pipeline, "_PROJECT_ROOT", Path(tmpdir)):
                found = grok_pipeline._find_accounts_file()
            self.assertEqual(found, path)

    def test_returns_empty_when_no_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.object(grok_pipeline, "_PROJECT_ROOT", Path(tmpdir)):
                found = grok_pipeline._find_accounts_file()
            self.assertEqual(found, "")


class TestMainEntryPoint(unittest.TestCase):
    """Verify main() dispatches to correct subcommand or prints help."""

    def test_no_args_exits_with_error(self):
        with self.assertRaises(SystemExit) as ctx:
            grok_pipeline.main([])
        self.assertEqual(ctx.exception.code, 1)

    def test_audit_dispatches(self):
        with mock.patch.object(grok_pipeline, "cmd_audit") as mock_audit:
            mock_audit.return_value = {}
            grok_pipeline.main(["audit"])
            mock_audit.assert_called_once()

    def test_mint_dispatches(self):
        with mock.patch.object(grok_pipeline, "cmd_mint") as mock_mint:
            mock_mint.return_value = (0, 0)
            grok_pipeline.main(["mint"])
            mock_mint.assert_called_once()

    def test_register_dispatches(self):
        with mock.patch.object(grok_pipeline, "cmd_register") as mock_reg:
            mock_reg.return_value = None
            grok_pipeline.main(["register"])
            mock_reg.assert_called_once()


class TestDoMint(unittest.TestCase):
    """Verify _do_mint logic without real browser."""

    def test_no_accounts_file_returns_zeros(self):
        with mock.patch.object(
            grok_pipeline, "_find_accounts_file", return_value=""
        ):
            with mock.patch.object(
                grok_pipeline, "_prepare_headless_display", return_value="normal"
            ):
                ok, fail = grok_pipeline._do_mint(limit=5)
        self.assertEqual(ok, 0)
        self.assertEqual(fail, 0)

    def test_all_already_minted_returns_zeros(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            accounts_file = os.path.join(tmpdir, "accounts.txt")
            with open(accounts_file, "w") as fh:
                fh.write("existing@test.com----pass----sso\n")

            with mock.patch.object(
                grok_pipeline, "_find_accounts_file", return_value=accounts_file
            ):
                with mock.patch.object(
                    grok_pipeline,
                    "_get_minted_emails",
                    return_value={"existing@test.com"},
                ):
                    with mock.patch.object(
                        grok_pipeline, "_prepare_headless_display", return_value="normal"
                    ):
                        ok, fail = grok_pipeline._do_mint(limit=5)
            self.assertEqual(ok, 0)
            self.assertEqual(fail, 0)

    def test_mint_processes_unminted_accounts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            accounts_file = os.path.join(tmpdir, "accounts.txt")
            with open(accounts_file, "w") as fh:
                fh.write("new@test.com----pass123----sso\n")

            mock_result = {
                "ok": True,
                "email": "new@test.com",
                "access_token": "tok",
                "refresh_token": "ref",
            }

            with mock.patch.object(
                grok_pipeline, "_find_accounts_file", return_value=accounts_file
            ):
                with mock.patch.object(
                    grok_pipeline, "_get_minted_emails", return_value=set()
                ):
                    with mock.patch.object(
                        grok_pipeline, "_prepare_headless_display", return_value="normal"
                    ):
                        with mock.patch(
                            "auto_import_grok_cli.mint_oauth", return_value=mock_result
                        ):
                            with mock.patch(
                                "auto_import_grok_cli.insert_to_9router", return_value=True
                            ):
                                with mock.patch(
                                    "auto_import_grok_cli.read_cpa_auth", return_value=None
                                ):
                                    ok, fail = grok_pipeline._do_mint(
                                        limit=1, sleep_sec=0, push_9router=True
                                    )
            self.assertEqual(ok, 1)
            self.assertEqual(fail, 0)

    def test_mint_handles_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            accounts_file = os.path.join(tmpdir, "accounts.txt")
            with open(accounts_file, "w") as fh:
                fh.write("fail@test.com----pass----sso\n")

            mock_result = {"ok": False, "error": "browser timeout"}

            with mock.patch.object(
                grok_pipeline, "_find_accounts_file", return_value=accounts_file
            ):
                with mock.patch.object(
                    grok_pipeline, "_get_minted_emails", return_value=set()
                ):
                    with mock.patch.object(
                        grok_pipeline, "_prepare_headless_display", return_value="normal"
                    ):
                        with mock.patch(
                            "auto_import_grok_cli.mint_oauth", return_value=mock_result
                        ):
                            ok, fail = grok_pipeline._do_mint(
                                limit=1, sleep_sec=0, push_9router=False
                            )
            self.assertEqual(ok, 0)
            self.assertEqual(fail, 1)


class TestGet9RouterEmails(unittest.TestCase):
    """Verify _get_9router_emails reads the DB correctly."""

    def test_reads_from_db(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "data.sqlite")
            conn = sqlite3.connect(db_path)
            conn.execute(
                """CREATE TABLE providerConnections (
                    id TEXT, provider TEXT, authType TEXT, name TEXT,
                    email TEXT, priority INT, isActive INT, data TEXT,
                    createdAt TEXT, updatedAt TEXT
                )"""
            )
            conn.execute(
                """INSERT INTO providerConnections VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "u1", "grok-cli", "oauth", "router@test.com",
                    "router@test.com", 1, 1, "{}", "2026-01-01", "2026-01-01",
                ),
            )
            conn.commit()
            conn.close()

            with mock.patch.object(grok_pipeline, "DB_PATH", db_path):
                emails = grok_pipeline._get_9router_emails()
            self.assertIn("router@test.com", emails)

    def test_missing_db_returns_empty(self):
        with mock.patch.object(grok_pipeline, "DB_PATH", "/nonexistent/db"):
            emails = grok_pipeline._get_9router_emails()
        self.assertEqual(emails, set())


class TestLogLine(unittest.TestCase):
    """Verify _log_line doesn't crash."""

    def test_log_line_runs(self):
        # Just ensure it doesn't raise
        grok_pipeline._log_line("[*] test message")


class TestSetupLogging(unittest.TestCase):
    """Verify logging setup does not crash and sets root level."""

    def test_setup_logging_runs_without_error(self):
        # _setup_logging uses basicConfig which is idempotent after first call;
        # just verify it doesn't raise.
        grok_pipeline._setup_logging(verbose=False)
        grok_pipeline._setup_logging(verbose=True)

    def test_setup_logging_force_level(self):
        import logging as _logging

        root = _logging.getLogger()
        old_level = root.level
        try:
            root.setLevel(_logging.WARNING)
            grok_pipeline._setup_logging(verbose=True)
            # basicConfig won't override if handlers exist, but the function
            # must not raise regardless.
        finally:
            root.setLevel(old_level)


if __name__ == "__main__":
    unittest.main()


class TestCmdSyncOracle(unittest.TestCase):
    def test_sync_oracle_parser_args(self):
        from grok_pipeline import build_parser
        parser = build_parser()
        args = parser.parse_args(["sync-oracle", "--limit", "5", "--dry-run", "--no-probe"])
        self.assertEqual(args.command, "sync-oracle")
        self.assertEqual(args.limit, 5)
        self.assertTrue(args.dry_run)
        self.assertFalse(args.probe)

    def test_push_oracle_alias(self):
        from grok_pipeline import build_parser
        parser = build_parser()
        args = parser.parse_args(["push-oracle", "--target-url", "http://example.com:20128"])
        self.assertEqual(args.command, "push-oracle")
        self.assertEqual(args.target_url, "http://example.com:20128")
