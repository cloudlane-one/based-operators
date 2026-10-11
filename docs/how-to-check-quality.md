# How to check quality

Certain checks are run by VSCode automatically as you edit code, while others have to be run manually using VSCode UI, tasks or commands in the terminal.

## Automatic checks

- Linting via `ruff`
- Type-checking via `pyright`

> Discovered issues by these tools will show up as yellow or red squiggly lines underlining your code.

## Manual checks

Pytest test cases can be run via the VSCode tests UI (beaker symbol in left sidebar) or `uv run pytest tests` in the terminal.

VSCode tasks can be run via the UI by clicking `ctrl` + `shift` + `P` to open the command palette and then typing `Run task`, or in the terminal via `vtr TASK_NAME`. Please run all tasks with the prefixes `assert-`, `build-` and `info-`. Make sure none of them fail and that their printed information does not hint at any major issues.

## The full gate, in one shot

When the editor squiggles go quiet, run the same checks CI runs before you call a
change done:

```shell
uv run ruff check .
uv run ruff format --check .
uv run pyright .
uv run pytest tests
uv run deptry .
uv run licensecheck --zero --ignore-licenses UNKNOWN --ignore-packages mypy-extensions -g app -g docs
uv run pip-audit .
SITE_URL="https://example.com/" uv run vtr build-docs
```

Every command should exit clean. `pytest` reporting skips is fine; failures are not.

## Gotchas that bite

- **`ruff` is opinionated here** — line length 100, Google-style docstrings, and
  `max-complexity = 12`. A long `if/elif` chain trips `C901`, so split it into small
  helpers rather than reaching for a `# noqa`.
- **`pyright` and Pydantic disagree about constructors** — Pydantic types `__init__`
  from the model's fields, so `Model(spec={...})` fails type-checking even though it
  runs fine. Build models with `model_validate({...})` or pass typed sub-models.
- **Docs execute code** — pages run their fenced blocks at build time via
  `markdown-exec`. Blocks that share state need a matching `session="..."` attribute,
  or the later block sees a `NameError`.
- **The end-to-end suite is opt-in** — `tests/test_integration.py` skips unless
  `BASED_OPERATORS_K8S_INTEGRATION=1` and a cluster is reachable via `kubectl`.
