PY ?= .venv/bin/python

.PHONY: check test lint type

check: lint type test

lint:
	$(PY) -m ruff check .

type:
	$(PY) -m mypy

test:
	$(PY) -m pytest -q -m "not demo"
