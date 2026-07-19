.PHONY: install lint typecheck test check

install:
	python -m pip install -e '.[dev]'

lint:
	ruff check .
	ruff format --check .

typecheck:
	mypy src

test:
	pytest --cov=letterboxd_scraper --cov-report=term-missing --cov-fail-under=70

check: lint typecheck test
