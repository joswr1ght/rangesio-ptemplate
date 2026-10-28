"""Exercise authoring validation and publishing boundaries without contacting the API."""

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests
import yaml

import package as pkg


UUID = "00000000-0000-0000-0000-000000000001"


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.campaign = self.root / "campaign.yaml"
        self.out = self.root / "dist/package.json"
        self.rio = pkg.load_campaign(Path(__file__).parent / "fixtures/campaign.yaml")
        self.env = patch.dict(os.environ, {"RIO_PACKAGE_ID": "", "RIOCLI": "", "RIO_CREDENTIALS": ""})
        self.env.start()
        self.addCleanup(self.env.stop)
        no_network = patch.object(requests.sessions.Session, "request",
                                  side_effect=AssertionError("Unexpected API call"))
        no_network.start()
        self.addCleanup(no_network.stop)
        quiet = patch("builtins.print")
        quiet.start()
        self.addCleanup(quiet.stop)

    def load(self, rio):
        self.campaign.write_text(yaml.safe_dump({"ranges_io": rio}))
        return pkg.load_campaign(self.campaign)

    def test_sample_build_is_deterministic_and_has_resolved_references(self):
        result = pkg.build_package(self.rio)
        self.assertEqual(result, pkg.build_package(self.rio))
        self.assertEqual(len(result["groups"]), 2)
        self.assertEqual(result["groups"][1]["unlock"]["groups"], [result["groups"][0]["id"]])
        self.assertEqual(result["triggers"][0]["conditions"][0]["targets"],
                         [result["groups"][0]["challenges"][0]["id"]])
        story = result["stories"][0]
        self.assertEqual(story["content"]["messages"][0]["actor"], story["content"]["actors"][0]["id"])
        self.assertNotIn("avatar", story["content"]["actors"][0])
        self.assertNotIn("uuid", result)

    def test_all_answer_modes_and_integer_debrief_types(self):
        cases = [
            {"mode": "Acknowledged"},
            {"mode": "Case sensitive", "value": "Alpha"},
            {"mode": "Case insensitive", "value": "alpha"},
            {"mode": "Integer", "value": 42},
            {"mode": "Prefixed", "value": "abc", "prefix": "flag"},
            {"mode": "Regex", "value": "abc", "validation": {
                "pattern": "^[a-z]+$", "message": "Lowercase letters", "tip": "Letters only"}},
            self.rio["challenges"][2]["answer"],
        ]
        for answer in cases:
            with self.subTest(mode=answer["mode"]):
                rio = deepcopy(self.rio)
                rio["challenges"][0]["answer"] = answer
                built = pkg.build_package(self.load(rio))["groups"][0]["challenges"][0]
                self.assertEqual(built["answer"]["mode"], answer["mode"])
                if answer["mode"] == "Integer":
                    self.assertIsInstance(built["answer"]["answers"][0]["value"], int)
                    self.assertEqual(built["debriefs"][0]["activatingAnswers"], ["42"])

    def test_invalid_authoring_is_rejected(self):
        cases = []
        for field, value in (("position", 7), ("briefing", ""), ("difficulty", "Impossible"),
                             ("external_reference", "file:///tmp/test"), ("short_titel", "Typo")):
            rio = deepcopy(self.rio)
            rio["challenges"][0][field] = value
            cases.append(rio)
        rio = deepcopy(self.rio)
        rio["challenges"][0]["answer"] = {"mode": "Integer", "value": "42"}
        cases.append(rio)
        rio = deepcopy(self.rio)
        rio["challenges"][2]["answer"]["options"][1]["correct"] = True
        cases.append(rio)
        rio = deepcopy(self.rio)
        rio["challenges"][2]["answer"]["options"][1]["value"] = rio["challenges"][2]["answer"]["options"][0]["value"]
        cases.append(rio)
        for rio in cases:
            with self.subTest(campaign=rio):
                with self.assertRaises(pkg.PackageError):
                    self.load(rio)

    def test_disjoint_groups_are_rejected(self):
        self.rio["challenges"][2]["group"] = "Getting Started"
        with self.assertRaisesRegex(pkg.PackageError, "not contiguous"):
            self.load(self.rio)

    def test_broken_story_references_are_rejected(self):
        for mutation in ("cast", "message", "trigger", "duplicate actor", "duplicate story"):
            rio = deepcopy(self.rio)
            if mutation == "cast":
                rio["stories"][0]["actors"] = ["unknown"]
            elif mutation == "message":
                rio["stories"][0]["messages"][0]["actor"] = "unknown"
            elif mutation == "trigger":
                rio["stories"][0]["trigger"]["challenge"] = 99
            elif mutation == "duplicate actor":
                rio["actors"].append(rio["actors"][0])
            else:
                rio["stories"].append(rio["stories"][0])
            with self.subTest(mutation=mutation), self.assertRaises(pkg.PackageError):
                self.load(rio)

    def test_duplicate_yaml_keys_are_rejected(self):
        self.campaign.write_text("ranges_io: {}\nranges_io: {}\n")
        with self.assertRaisesRegex(pkg.PackageError, "Duplicate YAML key"):
            pkg.load_campaign(self.campaign)

    def test_story_free_package(self):
        del self.rio["stories"]
        del self.rio["actors"]
        built = pkg.build_package(self.load(self.rio))
        self.assertEqual(built["stories"], [])
        self.assertEqual(built["triggers"], [])

    def test_missing_cli_has_actionable_error(self):
        with patch.object(pkg.shutil, "which", return_value=None), patch.object(Path, "is_file", return_value=False):
            with self.assertRaisesRegex(pkg.PackageError, "riocli not found"):
                pkg.find_riocli()

    def test_python_cli_uses_uv(self):
        with patch.object(pkg.shutil, "which", return_value="/example/riocli.py"):
            self.assertEqual(pkg.find_riocli(), ["uv", "run", "--quiet", "--script", "/example/riocli.py"])

    def test_explicit_cli_uses_argv_not_shell(self):
        os.environ["RIOCLI"] = 'uv run --script "/path with spaces/riocli.py"'
        self.assertEqual(pkg.find_riocli(), ["uv", "run", "--script", "/path with spaces/riocli.py"])

    def test_empty_credentials_are_rejected(self):
        path = self.root / "credentials"
        os.environ["RIO_CREDENTIALS"] = str(path)
        for value in ("", "\nsecond-line-is-not-a-token"):
            path.write_text(value)
            with self.assertRaises(pkg.PackageError):
                pkg.read_token()

    def test_doctor_detects_incompatible_cli(self):
        with patch.object(pkg, "find_riocli", return_value=["cli"]), patch.object(pkg, "run_cli", return_value="old"):
            with self.assertRaisesRegex(pkg.PackageError, "lacks required command"):
                pkg.doctor(self.rio)

    def test_offline_doctor_never_calls_api(self):
        help_text = "--config add-package --packagefile get-package --packageid list-permissions"
        with patch.object(pkg, "find_riocli", return_value=["cli"]), \
                patch.object(pkg, "read_token", return_value="secret"), \
                patch.object(pkg, "run_cli", return_value=help_text) as cli:
            pkg.doctor(self.rio)
            self.assertTrue(all(call.args[-1] == "--help" for call in cli.call_args_list))

    def test_cli_nonzero_and_timeout_fail(self):
        with patch.object(pkg.subprocess, "run", return_value=Mock(returncode=1)):
            with self.assertRaises(pkg.PackageError):
                pkg.run_cli(["cli"], "list-permissions")
        with patch.object(pkg.subprocess, "run", side_effect=subprocess.TimeoutExpired("cli", 60)):
            with self.assertRaises(subprocess.TimeoutExpired):
                pkg.run_cli(["cli"], "list-permissions")

    def test_deploy_requires_a_target(self):
        with patch.object(pkg.requests, "put") as put:
            with self.assertRaisesRegex(pkg.PackageError, "Set package.uuid"):
                pkg.deploy(self.rio, {}, self.out)
            put.assert_not_called()

    def test_deploy_backup_failure_prevents_put(self):
        self.rio["package"]["uuid"] = UUID
        with patch.object(pkg, "doctor", return_value=["cli"]), \
                patch.object(pkg, "export_package", return_value={}), \
                patch.object(pkg, "write_json", side_effect=OSError("disk full")), \
                patch.object(pkg.requests, "put") as put:
            with self.assertRaises(OSError):
                pkg.deploy(self.rio, {}, self.out)
            put.assert_not_called()

    def test_deploy_export_failure_prevents_put(self):
        self.rio["package"]["uuid"] = UUID
        with patch.object(pkg, "doctor", return_value=["cli"]), \
                patch.object(pkg, "export_package", side_effect=pkg.PackageError("Access denied")), \
                patch.object(pkg.requests, "put") as put:
            with self.assertRaises(pkg.PackageError):
                pkg.deploy(self.rio, {}, self.out)
            put.assert_not_called()

    def test_deploy_backs_up_then_updates_and_verifies(self):
        self.rio["package"]["uuid"] = UUID
        built = pkg.build_package(self.rio)
        def check_backup(*args, **kwargs):
            backups = list(self.out.parent.glob("backup-*.json"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(json.loads(backups[0].read_text()), {"groups": []})
            self.assertEqual(kwargs["json"], built)
            self.assertEqual(kwargs["timeout"], 60)
            return Mock()
        with patch.object(pkg, "doctor", return_value=["cli"]), \
                patch.object(pkg, "read_token", return_value="secret"), \
                patch.object(pkg, "export_package", side_effect=[{"groups": []}, built]), \
                patch.object(pkg.requests, "put", side_effect=check_backup):
            pkg.deploy(self.rio, built, self.out)
        self.assertEqual(len(list(self.out.parent.glob("export-*.json"))), 1)

    def test_deploy_http_failure_preserves_backup(self):
        self.rio["package"]["uuid"] = UUID
        response = Mock()
        response.raise_for_status.side_effect = requests.HTTPError("403")
        with patch.object(pkg, "doctor", return_value=["cli"]), \
                patch.object(pkg, "read_token", return_value="secret"), \
                patch.object(pkg, "export_package", return_value={"groups": []}) as export, \
                patch.object(pkg.requests, "put", return_value=response):
            with self.assertRaisesRegex(pkg.PackageError, "Update failed"):
                pkg.deploy(self.rio, {}, self.out)
            self.assertEqual(export.call_count, 1)
            self.assertEqual(len(list(self.out.parent.glob("backup-*.json"))), 1)

    def test_verification_accepts_only_documented_normalizations(self):
        sent = pkg.build_package(self.rio)
        got = deepcopy(sent)
        got["token"] = "server-generated"
        got["notes"] = sent["notes"].replace("\n", "\r\n")
        got["stories"][0]["content"]["actors"][0]["avatar"] = None
        got["groups"][0]["challenges"][0]["answer"]["answers"][0]["dimensions"].append({"id": "other", "value": 0})
        pkg.verify(sent, got)
        got["groups"][1]["challenges"][0]["answer"]["answers"][0]["value"] = "incorrect"
        with self.assertRaises(pkg.PackageError):
            pkg.verify(sent, got)

    def test_publish_refuses_an_existing_target(self):
        self.rio["package"]["uuid"] = UUID
        with patch.object(pkg, "doctor") as doctor:
            with self.assertRaisesRegex(pkg.PackageError, "already configured"):
                pkg.publish(self.rio, {}, self.out)
            doctor.assert_not_called()

    def test_failed_online_preflight_prevents_publication(self):
        with patch.object(pkg, "doctor", side_effect=pkg.PackageError("Authentication failed")), \
                patch.object(pkg, "cli_json") as cli:
            with self.assertRaises(pkg.PackageError):
                pkg.publish(self.rio, {}, self.out)
            cli.assert_not_called()
            self.assertFalse((self.out.parent / "publish-response.json").exists())

    def test_publish_timeout_leaves_a_receipt_and_blocks_retry(self):
        with patch.object(pkg, "doctor", return_value=["cli"]), \
                patch.object(pkg, "cli_json", side_effect=subprocess.TimeoutExpired("cli", 60)):
            with self.assertRaises(subprocess.TimeoutExpired):
                pkg.publish(self.rio, {}, self.out)
        self.assertTrue((self.out.parent / "publish-response.json").is_file())
        with self.assertRaisesRegex(pkg.PackageError, "previous publish receipt"):
            pkg.publish(self.rio, {}, self.out)

    def test_publish_uses_cli_add_package_and_saves_response(self):
        built = pkg.build_package(self.rio)
        with patch.object(pkg, "doctor", return_value=["cli"]), \
                patch.object(pkg, "cli_json", return_value={"package": {"id": UUID}}) as cli, \
                patch.object(pkg, "export_package", return_value=built):
            pkg.publish(self.rio, built, self.out)
            self.assertIn("add-package", cli.call_args.args)
            self.assertIn(str(self.out), cli.call_args.args)
        self.assertEqual(json.loads((self.out.parent / "publish-response.json").read_text())["package"]["id"], UUID)


if __name__ == "__main__":
    unittest.main()
