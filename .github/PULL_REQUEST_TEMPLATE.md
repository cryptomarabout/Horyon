<!-- Keep it focused: one logical change per PR. -->

## What & why

<!-- What does this change, and what problem does it solve? -->

## How I tested it

<!-- Commands run, output observed. -->

## Checklist

- [ ] No secrets committed (real values stay in `.env`)
- [ ] New config values documented in `.env.example`
- [ ] `python -m py_compile $(git ls-files '*.py')` passes
- [ ] Docs updated if this changes architecture / schema / a component contract
