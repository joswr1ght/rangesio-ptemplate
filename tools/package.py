#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6,<7", "jsonschema>=4,<5", "requests>=2,<3"]
# ///
"""Build portable Ranges.io packages and publish with explicit create/update actions."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import unittest
from uuid import UUID

import jsonschema
import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]
PKG = "package_1"
DIM = f"{PKG}_dimension_1"
API = "https://ranges.io/api/v1/package"


class PackageError(Exception):
    """An actionable authoring or publishing failure."""


class UniqueLoader(yaml.SafeLoader):
    """Reject duplicate mapping keys instead of silently overwriting content."""


def unique_mapping(loader, node):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if key in mapping:
            raise PackageError(f"Duplicate YAML key {key!r} at line {key_node.start_mark.line + 1}")
        mapping[key] = loader.construct_object(value_node, deep=True)
    return mapping


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def load_campaign(path):
    data = yaml.load(Path(path).read_text(), Loader=UniqueLoader)
    schema = json.loads((ROOT / "schema/campaign.schema.json").read_text())
    errors = sorted(jsonschema.Draft202012Validator(schema).iter_errors(data), key=lambda err: str(err.path))
    if errors:
        messages = [f"{'.'.join(map(str, err.absolute_path)) or '<root>'}: {err.message}" for err in errors]
        raise PackageError("Invalid campaign:\n  " + "\n  ".join(messages))
    rio = data["ranges_io"]
    challenges = rio["challenges"]
    if [ch["position"] for ch in challenges] != list(range(1, len(challenges) + 1)):
        raise PackageError("Challenge positions should be consecutive, starting at 1, in YAML order")
    seen, previous = set(), None
    for ch in challenges:
        group = ch["group"]
        if group != previous and group in seen:
            raise PackageError(f"Group {group!r} is not contiguous")
        seen.add(group)
        previous = group
        answer = ch["answer"]
        if answer["mode"] == "Multiple choice":
            options = answer["options"]
            if sum(option["correct"] for option in options) != 1:
                raise PackageError(f"Challenge {ch['position']}: exactly one option should be correct")
            if len({option['value'] for option in options}) != len(options):
                raise PackageError(f"Challenge {ch['position']}: duplicate answer options")
    roster = {actor["id"] for actor in rio.get("actors", [])}
    if len(roster) != len(rio.get("actors", [])):
        raise PackageError("Duplicate actor IDs")
    story_ids = set()
    for story in rio.get("stories", []):
        if story["id"] in story_ids:
            raise PackageError(f"Duplicate story ID {story['id']!r}")
        story_ids.add(story["id"])
        cast = set(story["actors"])
        if not cast <= roster:
            raise PackageError(f"Story {story['id']}: unknown actors {sorted(cast - roster)}")
        if story["trigger"]["challenge"] > len(challenges):
            raise PackageError(f"Story {story['id']}: trigger challenge does not exist")
        for message in story["messages"]:
            if message["actor"] not in cast:
                raise PackageError(f"Story {story['id']}: message actor is absent from its cast")
    return rio


def dimensions(value):
    return [{"id": DIM, "value": value}]


def build_answer(answer):
    mode = answer["mode"]
    if mode == "Multiple choice":
        return {"mode": mode, "random": answer.get("random", True), "answers": [
            {"correct": option["correct"], "dimensions": dimensions(option["points"]),
             "state": "Enabled", "value": option["value"]} for option in answer["options"]]}
    result = {"mode": mode, "answers": [
        {"correct": True, "dimensions": dimensions(answer.get("points", 0)),
         "state": "Enabled", "value": "" if mode == "Acknowledged" else answer["value"]}]}
    for key in ("text", "prefix", "validation"):
        if key in answer:
            result[key] = answer[key]
    if mode == "Acknowledged":
        result.setdefault("text", "Continue")
    return result


def build_package(rio):
    groups, positions = [], {}
    penalty = rio.get("hint_policy", {}).get("penalty", 0)
    for ch in rio["challenges"]:
        if not groups or groups[-1]["name"] != ch["group"]:
            gid = f"{PKG}_group_{len(groups) + 1}"
            unlock = {"mode": "Unlocked"} if not groups else {
                "mode": "Progressive", "groups": [groups[-1]["id"]], "inclusionMode": "All", "percentage": 100}
            groups.append({"id": gid, "name": ch["group"], "intro": "", "orderMode": "Sequential",
                           "unlock": unlock, "challenges": [], "challengeAttemptLimit": {"enabled": False},
                           "challengePenalty": {"enabled": False}})
        group = groups[-1]
        cid = f"{group['id']}_challenge_{len(group['challenges']) + 1}"
        positions[ch["position"]] = cid
        answer = build_answer(ch["answer"])
        correct = next(option["value"] for option in answer["answers"] if option["correct"])
        debriefs = []
        if ch.get("debrief"):
            debriefs = [{"title": "Debrief", "content": ch["debrief"].strip(),
                         "activationMode": "Specific answers", "activatingAnswers": [str(correct)]}]
        group["challenges"].append({
            "id": cid, "shortTitle": ch["short_title"], "longTitle": ch.get("long_title", ch["short_title"]),
            "briefing": ch["briefing"].strip(), "answer": answer, "debriefs": debriefs,
            "difficulty": ch.get("difficulty", "Medium"), "externalReference": ch.get("external_reference", ""),
            "hints": {"penalty": penalty, "options": [
                {"title": f"Hint {index}", "content": text.strip()}
                for index, text in enumerate(ch.get("hints", []), 1)]},
            "attemptLimit": None, "penalty": None, "primaryDimensionDisplayMode": "Package default",
            "files": [], "customTags": [], "tags": [], "notes": ""})

    roster = {actor["id"]: actor for actor in rio.get("actors", [])}
    stories, triggers = [], []
    for index, story in enumerate(rio.get("stories", []), 1):
        sid, tid = f"{PKG}_story_{index}", f"{PKG}_trigger_{index}"
        actors, actor_ids = [], {}
        for ai, ref in enumerate(story["actors"], 1):
            actor = roster[ref]
            aid = f"{sid}_actor_{ai}"
            actor_ids[ref] = aid
            entry = {"id": aid, "name": actor["name"], "appearAsPlayer": actor.get("appear_as_player", False)}
            if actor.get("avatar"):
                entry["avatar"] = actor["avatar"]
            actors.append(entry)
        messages = [{"id": f"{sid}_message_{mi}", "actor": actor_ids[message["actor"]],
                     "delay": message.get("delay", 1500), "text": message["text"]}
                    for mi, message in enumerate(story["messages"], 1)]
        stories.append({"id": sid, "name": story["name"], "type": "Chat",
                        "autoplay": story.get("autoplay", True), "content": {"actors": actors, "messages": messages}})
        triggers.append({"id": tid, "trigger_name": story["name"], "enabled": True,
                         "achievement": None, "activation_limit": None, "activation_mode": "Any",
                         "conditions": [{"id": f"{tid}_condition_1", "type": "CHALLENGES",
                                         "targets": [positions[story["trigger"]["challenge"]]],
                                         "activation_type": "All", "attempt_status": "Correct answer",
                                         "answers": [], "number_of_attempts": 0}],
                         "effects": [{"id": f"{tid}_effect_1", "type": "STORY_UNLOCK", "run_for": "Scorer",
                                      "targets": [sid]}]})
    return {"id": PKG, "name": rio["package"]["name"], "notes": rio["package"].get("notes", ""),
            "challengeHintsRevealMode": "Allow hint reveals", "scoringMode": "Infinite",
            "primaryDimensionDisplayMode": "Max", "scoreRankingMode": "Primary only",
            "files": [], "parameters": [],
            "dimensions": [{"id": DIM, "label": "Points", "unit": "pts", "unitPlacement": "After value",
                            "isPrimary": True, "precision": "No decimal place", "startingValue": 0,
                            "displayMode": "Before and after completion"}],
            "groups": groups, "stories": stories, "triggers": triggers}


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def target_id(rio):
    value = os.environ.get("RIO_PACKAGE_ID") or rio["package"].get("uuid")
    if value:
        try:
            return str(UUID(value))
        except ValueError as exc:
            raise PackageError("RIO_PACKAGE_ID or package.uuid is not a UUID") from exc
    return None


def credentials_path():
    return Path(os.environ.get("RIO_CREDENTIALS") or "~/.rio/credentials").expanduser()


def read_token():
    lines = credentials_path().read_text().splitlines()
    if not lines or not lines[0].strip():
        raise PackageError(f"No API token on the first line of {credentials_path()}")
    return lines[0].strip()


def find_riocli():
    override = os.environ.get("RIOCLI")
    if override:
        return [os.path.expanduser(part) for part in shlex.split(override)]
    candidate = shutil.which("riocli") or shutil.which("riocli.py")
    script = Path(candidate) if candidate else Path.home() / "Dev/riocli/riocli.py"
    if not candidate and not script.is_file():
        raise PackageError("riocli not found. Install it on PATH or set RIOCLI='uv run --script /path/to/riocli.py'")
    if script.suffix == ".py":
        return ["uv", "run", "--quiet", "--script", str(script)]
    return [str(script)]


def run_cli(command, *args):
    result = subprocess.run([*command, *args], capture_output=True, text=True, timeout=60, check=False)
    if result.returncode:
        # Do not print credentials or arbitrary API response bodies in errors.
        verb = args[2] if args[:1] == ("-c",) else args[0]
        raise PackageError(f"riocli {verb} failed (exit {result.returncode}); "
                           "check CLI dependencies, credentials, network access, and API permissions")
    return result.stdout


def cli_json(command, *args):
    try:
        return json.loads(run_cli(command, *args))
    except json.JSONDecodeError as exc:
        raise PackageError("riocli did not return valid JSON") from exc


def export_package(command, uuid):
    response = cli_json(command, "-c", str(credentials_path()), "get-package", "--packageid", uuid)
    try:
        package = response["package"]["export"]
        if not isinstance(package, dict) or not isinstance(package["groups"], list):
            raise KeyError("groups")
        return package
    except (KeyError, TypeError) as exc:
        raise PackageError("The target package could not be exported; check its UUID and API access") from exc


def doctor(rio, online=False):
    command = find_riocli()
    if not command:
        raise PackageError("RIOCLI is empty")
    help_text = run_cli(command, "--help")
    for verb, flag in (("add-package", "--packagefile"), ("get-package", "--packageid"),
                       ("list-permissions", None)):
        if verb not in help_text:
            raise PackageError(f"riocli lacks required command {verb}")
        if flag and flag not in run_cli(command, verb, "--help"):
            raise PackageError(f"riocli {verb} lacks required flag {flag}")
    if "--config" not in help_text:
        raise PackageError("riocli lacks the required --config option")
    read_token()
    print("OK: riocli runs, publishing commands are available, and local credentials are nonempty")
    uuid = target_id(rio)
    if online:
        permissions = run_cli(command, "-c", str(credentials_path()), "list-permissions").strip()
        if not permissions:
            raise PackageError("The API returned no permissions for this token")
        print(f"API permission report (read-only):\n{permissions}")
        if uuid:
            export_package(command, uuid)
            print(f"OK: target {uuid} can be exported")
        print("OK: API authentication succeeded; no write was attempted")
    return command


def normalized(node):
    if isinstance(node, dict):
        return {key: normalized(value) for key, value in node.items()
                if key != "token" and not (key == "avatar" and value is None)}
    if isinstance(node, list):
        # The server may add zero-valued scoring dimensions.
        if node and all(isinstance(item, dict) and set(item) == {"id", "value"} for item in node):
            node = sorted((item for item in node if item["value"] != 0), key=lambda item: item["id"])
        return [normalized(value) for value in node]
    return node.replace("\r\n", "\n") if isinstance(node, str) else node


def verify(sent, got):
    if normalized(sent) != normalized(got):
        raise PackageError("Round-trip verification failed. Compare the generated JSON with the saved export")


def publish(rio, package, out, campaign):
    if target_id(rio):
        raise PackageError("A target UUID is already configured. Use make deploy to update it")
    receipt = out.parent / "publish-response.json"
    if receipt.exists():
        raise PackageError(f"A previous publish receipt exists at {receipt}. Configure its UUID and use make deploy")
    if any(actor.get("avatar") for actor in rio.get("actors", [])):
        raise PackageError("New packages cannot reuse package-scoped avatars. Omit avatar references before publishing")
    command = doctor(rio, online=True)
    # A receipt is written before POST: a timeout can leave a created package.
    write_json(receipt, {"status": "pending", "note": "Check list-packages before retrying a failed publish"})
    response = cli_json(command, "-c", str(credentials_path()), "add-package", "--packagefile", str(out))
    write_json(receipt, response)
    details = response.get("package", {}) if isinstance(response, dict) else {}
    uuid = details.get("id") if isinstance(details, dict) else None
    if not uuid:
        print(f"Create request completed. Response: {receipt}. Confirm the new ID with riocli list-packages; "
              f"then set ranges_io.package.uuid in {campaign}")
        return
    print(f"Published {uuid}. Before make deploy, edit {campaign} and set ranges_io.package.uuid: {uuid}")
    got = export_package(command, str(UUID(uuid)))
    write_json(out.parent / "publish-export.json", got)
    verify(package, got)
    print("OK: new package round-trip verified")


def deploy(rio, package, out):
    uuid = target_id(rio)
    if not uuid:
        raise PackageError("Set package.uuid or RIO_PACKAGE_ID before deploying. Use make publish for a NEW package")
    command = doctor(rio, online=True)
    baseline = export_package(command, uuid)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = out.parent / f"backup-{uuid}-{stamp}.json"
    write_json(backup, baseline)
    print(f"Backup: {backup}")
    try:
        response = requests.put(f"{API}/{uuid}", headers={"Authorization": f"Bearer {read_token()}"},
                                json=package, timeout=60)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise PackageError(f"Update failed or its result is unknown. Backup: {backup}. "
                           "Check the live package before retrying") from exc
    got = export_package(command, uuid)
    export_path = out.parent / f"export-{uuid}-{stamp}.json"
    write_json(export_path, got)
    verify(package, got)
    print(f"OK: updated {uuid} and verified the round trip. Export: {export_path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "build", "doctor", "publish", "deploy", "test"))
    parser.add_argument("--campaign", default="campaign/campaign.yaml")
    parser.add_argument("--out", type=Path, default=Path("dist/rangesio-package.json"))
    parser.add_argument("--online", action="store_true", help="also perform read-only API checks")
    args = parser.parse_args()
    if args.action == "test":
        sys.path.insert(0, str(ROOT / "tools"))
        suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
        return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1
    try:
        rio = load_campaign(args.campaign)
        target_id(rio)
        if args.action == "doctor":
            doctor(rio, args.online)
            return 0
        package = build_package(rio)
        print(f"OK: {package['name']}: {len(package['groups'])} groups, {len(rio['challenges'])} challenges, "
              f"{len(package['stories'])} stories")
        if args.action == "check":
            return 0
        write_json(args.out, package)
        print(f"Built {args.out}")
        if args.action == "publish":
            publish(rio, package, args.out, args.campaign)
        elif args.action == "deploy":
            deploy(rio, package, args.out)
    except (PackageError, OSError, ValueError, TypeError, yaml.YAMLError, subprocess.TimeoutExpired) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
