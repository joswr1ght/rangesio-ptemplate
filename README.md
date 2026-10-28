# Ranges.io Package Template

This template lets authors create and maintain Ranges.io packages as YAML files.
Authors write challenge briefings, answers, hints, debriefs, and chat stories in
`campaign/campaign.yaml`, validate the content locally, and publish it to
Ranges.io from the command line. The YAML remains the source of truth for later
updates, so package content can be reviewed and versioned alongside other project
files instead of maintained in the Ranges.io authoring interface.

The included investigation demonstrates three checkpoints across two groups,
scored answers, a hint, debriefs, and a chat story that plays after the opening
checkpoint. Replace this content with the new package's material.

## Requirements

Local authoring requires `make`, [uv](https://docs.astral.sh/uv/), and Python 3.11
or later. `uv` resolves the Python dependencies declared in `tools/package.py`.
The first run may download Python or dependencies.

Publishing also requires [riocli](https://github.com/joswr1ght/riocli) and a
Ranges.io API token with access to create or update packages. Local validation,
builds, and tests work without Ranges.io credentials.

## Start a package

1. Create a repository for the new package from this template. On GitHub, select
   **Use this template** and create a private repository, or use the GitHub CLI:

   ```sh
   gh repo create my-rangesio-package --private \
     --template joswr1ght/rangesio-ptemplate --clone
   cd my-rangesio-package
   ```

   Without GitHub, copy the files and start a new history:

   ```sh
   git clone --depth 1 https://github.com/joswr1ght/rangesio-ptemplate.git my-rangesio-package
   cd my-rangesio-package
   rm -rf .git && git init
   ```

   The campaign YAML contains every correct answer. Keep package repositories
   private unless the answers are meant to be public.

2. Edit `campaign/campaign.yaml`. Set the package name and notes, replace the
   sample challenges, and adapt or remove the sample actors and story. Keep
   `ranges_io.package.uuid` set to `null` until the first publication.

3. Validate and build the package:

   ```sh
   make check
   make build
   ```

   A successful check reports the package name and counts of groups, challenges,
   and stories. The build writes `dist/rangesio-package.json` for publication.
   `make package` is an alias for `make build`.

The generated JSON contains correct answers. Keep it with author materials rather
than distributing it to learners. Make content changes in YAML and rebuild the
JSON before publishing.

## Project files

| Path | Purpose |
| --- | --- |
| `campaign/campaign.yaml` | Package content and the target package UUID after publication. |
| `schema/campaign.schema.json` | YAML field definitions and editor validation. |
| `tools/package.py` | Validation, JSON generation, publishing, and deployment. |
| `tests/test_package.py` | Local tests for authoring and publishing behavior. |
| `tests/fixtures/campaign.yaml` | Fixed test content, independent of the authored campaign. |
| `Makefile` | Commands for the authoring and publishing workflow. |
| `LICENSE` | MIT license for the template. |
| `dist/` | Generated packages, publish receipts, backups, and verification exports. |

Keep the YAML, schema, tooling, and tests in the package's version-control
repository. The included `.gitignore` excludes generated output and local
credentials.

## Write package content

A campaign contains package metadata and an ordered list of challenges under
`ranges_io`. This is a complete minimal campaign:

```yaml
# yaml-language-server: $schema=../schema/campaign.schema.json
ranges_io:
  package:
    name: Request Analysis
    notes: An exercise in interpreting application access logs.
    uuid: null
  challenges:
    - position: 1
      group: Investigation
      short_title: Identify the Requested File
      briefing: |
        An application logged a GET request with `path=/etc/passwd`.

        What file path did the client request?
      answer:
        mode: Case sensitive
        value: /etc/passwd
        points: 100
      debrief: The path field identifies the requested resource as `/etc/passwd`.
```

Retain the schema comment for validation in editors that support the YAML language
server. `make check` also validates the schema, rejects duplicate keys and unknown
fields, and checks challenge ordering and story references.

### Challenges and groups

Each challenge has a `position`, `group`, `short_title`, `briefing`, and `answer`.
Positions are consecutive starting at 1 and follow YAML order. Challenges sharing
a group name stay adjacent. The first group starts unlocked, and each later group
unlocks after completion of the previous group. Challenges run sequentially within
each group.

Briefings, hints, and debriefs support Markdown. Use YAML `|` to preserve line
breaks for paragraphs, lists, and code blocks, or `>` to fold prose into a paragraph.

| Optional challenge field | Behavior |
| --- | --- |
| `long_title` | Expanded title; defaults to `short_title`. |
| `difficulty` | `Easy`, `Medium`, or `Hard`; defaults to `Medium`. |
| `external_reference` | An HTTP or HTTPS URL associated with the challenge. |
| `hints` | A list of Markdown strings, displayed as numbered hints. |
| `debrief` | Markdown displayed after the correct answer. |

Set `ranges_io.hint_policy.penalty` to the point penalty for revealing a hint.
The default is 0. The generated package uses a single scoring dimension named
`Points`, allows hint reveals, and uses the `Infinite` scoring mode.

### Answers and scoring

Answer mode names are case-sensitive. The fields below belong inside `answer`:

| Mode | Fields |
| --- | --- |
| `Acknowledged` | Optional `text` for the button label, defaulting to `Continue`. |
| `Case sensitive` | `value` as a string. |
| `Case insensitive` | `value` as a string. |
| `Integer` | `value` as an unquoted integer. |
| `Multiple choice` | `options`, each with `value`, `correct`, and `points`; optional `random`, defaulting to `true`. |
| `Prefixed` | `value` and `prefix` as strings. |
| `Regex` | `value` plus `validation` containing `pattern`, `message`, and `tip` strings. |

All modes except `Multiple choice` accept `points`, defaulting to 0.
Multiple-choice questions require at least two distinct options and exactly one
correct option; each option carries its own point value. The sample campaign
includes a complete multiple-choice question.

Quote textual answers that resemble numbers or YAML booleans, such as `"42"` or
`"yes"`. Keep `Integer` answers unquoted. Regex validation patterns use the
platform's regular-expression syntax; check their behavior in the published
package.

### Actors and chat stories

Define named actors in `ranges_io.actors`, then reference their IDs in
`ranges_io.stories`. Each story lists its cast, a trigger challenge position, and
messages. A message's actor belongs to that story's cast.

A story unlocks when the triggering challenge receives a correct answer. Stories
default to `autoplay: true`. Message `delay` values are milliseconds and default
to 1500 when omitted. The sample campaign demonstrates a two-message handoff
triggered by challenge 1.

Actors may set `appear_as_player: true` or provide an `avatar` reference. For
avatars, first publish the package, upload the images through Ranges.io, and obtain
their references from that package's export. Avatar references are package-scoped,
so use them only when deploying to that same package. First publication requires
actors without avatar references.

For a package consisting only of challenges, omit both `actors` and `stories`.

## Configure publishing

Install riocli according to its repository instructions. The publishing tool looks
for `riocli` or `riocli.py` on `PATH`, then for `~/Dev/riocli/riocli.py`. A discovered
Python script runs through `uv` to resolve its dependencies. For another location,
set `RIOCLI` to the command that runs it:

```sh
export RIOCLI='uv run --script /path/to/riocli.py'
```

Store the Ranges.io API token on the first line of `~/.rio/credentials`, outside
the project, and restrict the file to the current user:

```sh
mkdir -p ~/.rio
printf '%s\n' 'YOUR-API-TOKEN' > ~/.rio/credentials
chmod 600 ~/.rio/credentials
```

Set `RIO_CREDENTIALS` to use a different credentials file:

```sh
export RIO_CREDENTIALS=/path/to/credentials
```

Check local setup, then authenticate and review the token's API permissions:

```sh
make doctor
make doctor-online
```

`doctor` checks riocli commands, required flags, and a nonempty credentials file.
`doctor-online` reports API permissions and, when a target UUID is configured,
checks that the package can be exported. These are read-only API checks. Review
the permission report for the intended create or update operation.

## Publish a new package

Review the generated JSON, then create the package:

```sh
make build
make publish
```

`publish` rebuilds the JSON, runs the publishing checks, and creates a package
using `riocli add-package`. It saves the response to `dist/publish-response.json`.
When the response includes a package ID, the tool also saves
`dist/publish-export.json` and verifies it against the generated content.

Copy the returned ID into `ranges_io.package.uuid` in the campaign YAML. This
identifies the package for future updates. If the response omits the ID, locate
the new package with `riocli list-packages` or the equivalent command configured
in `RIOCLI`.

Open the resulting package in the Ranges.io player and check answers, hints,
debriefs, story playback, and group progression before making it available to
learners.

### Recover from an interrupted publication

A publish receipt is written before the create request. A timeout or failed
verification can occur after the server has created the package. Check the saved
receipt and the package list before retrying. If the package exists, record its
UUID in YAML and continue with `make deploy`. If the package was not created,
move the receipt aside before retrying `make publish`.

`publish` refuses to create another package when a target UUID or an existing
publish receipt is present.

## Update a published package

Edit the campaign YAML, review the changes, and deploy to its configured UUID:

```sh
make check
make deploy
```

To supply the target without changing YAML, use an override:

```sh
make deploy RIO_PACKAGE_ID=00000000-0000-0000-0000-000000000001
```

Replace the example UUID with the intended package ID. `RIO_PACKAGE_ID` takes
precedence over `ranges_io.package.uuid`.

Deployment replaces package content rather than merging it. Changes made only in
the Ranges.io authoring interface can be overwritten, so incorporate content
changes into YAML before deploying. The builder generates internal challenge and
story IDs from their order; review insertions, deletions, and reordering carefully
when updating an existing package.

The deployment workflow builds and validates the package, runs publishing checks,
exports a timestamped backup, updates the target, and exports it again for
comparison. Updates use the Ranges.io package API with the configured credentials.
A failed backup prevents the update. Backups and verification exports are saved
beside the generated JSON.

Verification ignores server-generated tokens, line-ending differences, omitted
null avatars, and zero-valued scoring dimensions. Other differences cause the
command to fail. If an update times out or verification fails, inspect the live
package and compare the saved files before retrying. Review the package in the
player after a successful deployment as well.

## Commands and alternate paths

| Command | Purpose |
| --- | --- |
| `make check` | Validate and check content locally. |
| `make build` | Validate and generate package JSON. |
| `make doctor` | Check publishing tools and local credentials. |
| `make doctor-online` | Authenticate, report permissions, and check target read access. |
| `make test` | Run isolated builder and publisher tests. |
| `make publish` | Create a new package. |
| `make deploy` | Back up, replace, and verify an existing package. |
| `make clean` | Remove `dist/`, including receipts, backups, and exports. |

`check`, `build`, and `test` do not contact the Ranges.io API. Preserve any needed
backups and receipts elsewhere before running `clean`.

Make variables select an alternate campaign and output path:

```sh
make build CAMPAIGN=campaign/other.yaml PACKAGE=dist/other/package.json
make publish CAMPAIGN=campaign/other.yaml PACKAGE=dist/other/package.json
```

Use the same paths for subsequent deployments. For multiple packages in one
repository, give each package a separate output directory because publish receipts
and exports are stored beside the generated JSON. `RIOCLI`, `RIO_CREDENTIALS`, and
`RIO_PACKAGE_ID` can also be supplied as Make overrides or environment variables.

## License

This template is available under the [MIT License](LICENSE). Packages built from
it belong to their authors and may use any license.
