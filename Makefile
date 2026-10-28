.DEFAULT_GOAL := help

UV ?= uv
CAMPAIGN ?= campaign/campaign.yaml
PACKAGE ?= dist/rangesio-package.json
# Optional: RIOCLI='uv run --script /path/to/riocli.py'
# Optional: RIO_CREDENTIALS=/path/to/credentials RIO_PACKAGE_ID=<uuid>
export RIOCLI RIO_CREDENTIALS RIO_PACKAGE_ID
RUN = $(UV) run --quiet --script tools/package.py

.PHONY: help check build package doctor doctor-online test publish deploy clean

help:
	@echo "make check          Validate campaign YAML without credentials or API calls"
	@echo "make build          Check and build dist/rangesio-package.json"
	@echo "make doctor         Check riocli commands and local credentials (offline)"
	@echo "make doctor-online  Report API permissions and check target access (read-only)"
	@echo "make test           Run isolated builder and publisher tests (no API calls)"
	@echo "make publish        Create a NEW package using riocli add-package"
	@echo "make deploy         Replace the configured package, back up, and verify"
	@echo "make clean          Remove generated dist/ files"

check:
	$(RUN) check --campaign "$(CAMPAIGN)"

build package:
	$(RUN) build --campaign "$(CAMPAIGN)" --out "$(PACKAGE)"

doctor:
	$(RUN) doctor --campaign "$(CAMPAIGN)"

doctor-online:
	$(RUN) doctor --online --campaign "$(CAMPAIGN)"

test:
	$(RUN) test

publish:
	$(RUN) publish --campaign "$(CAMPAIGN)" --out "$(PACKAGE)"

deploy:
	$(RUN) deploy --campaign "$(CAMPAIGN)" --out "$(PACKAGE)"

clean:
	rm -rf dist
