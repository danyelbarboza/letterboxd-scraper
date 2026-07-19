# Contributing

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

## Quality checks

Run all checks before opening a pull request:

```bash
make check
```

## Design guidelines

- Keep HTTP, parsing, orchestration, validation, and output concerns separate.
- Prefer immutable dataclasses for domain values.
- Keep parsers pure and cover every discovered markup variation with a regression test.
- Never treat an empty or implausibly small dataset as success.
- Use canonical Letterboxd film URLs as identifiers.
- Add comments only when they explain a non-obvious operational constraint or design decision.
- Do not add authenticated scraping or mechanisms intended to bypass access controls.

## Commit and pull request scope

Keep changes focused. A parser change should include a fixture or test that demonstrates the old failure and the new behavior.
