# How to check quality

Certain checks are run by VSCode automatically as you edit code, while others have to be run manually using VSCode UI, tasks or commands in the terminal.

## Automatic checks

- Linting via `ruff`
- Type-checking via `pyright`

> Discovered issues by these tools will show up as yellow or red squiggly lines underlining your code.

## Manual checks

Pytest test cases can be run via the VSCode tests UI (beaker symbol in left sidebar) or `uv run pytest tests` in the terminal.

VSCode tasks can be run via the UI by clicking `ctrl` + `shift` + `P` to open the command palette and then typing `Run task`, or in the terminal via `vtr TASK_NAME`. Please run all tasks with the prefixes `assert-`, `build-` and `info-`. Make sure none of them fail and that their printed information does not hint at any major issues.

To verify generated deployment artifacts locally, run `BASED_OPERATORS_REQUIRE_HELM=1 uv run pytest tests/src/test_build.py` (requires Helm) and `BASED_OPERATORS_CONTAINER_SMOKE=1 uv run pytest tests/src/test_container.py` (requires a running Docker daemon and network access for the pinned image and Git dependency). The checks job runs both; without those environment flags, unavailable local tools cause the corresponding integration checks to skip. The container test builds a downstream fixture, runs it as a non-root user, verifies the installed library and registered handler, then removes its test image.
