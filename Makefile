.PHONY: install test test-cov lint typecheck clean run-ui run-scheduler

install:
	python -m venv venv && \
	. venv/bin/activate && \
	pip install -e ".[dev,test]"

test:
	python -m pytest tests/ -v --tb=short

test-cov:
	python -m pytest tests/ -v --tb=short --cov=slik_checker --cov-report=term --cov-report=html

lint:
	python -m ruff check slik_checker/

lint-fix:
	python -m ruff check --fix slik_checker/

typecheck:
	python -m mypy slik_checker/ --ignore-missing-imports

format:
	python -m ruff format slik_checker/

check: lint test typecheck
	@echo "All checks passed"

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov dist build *.egg-info

run-ui:
	python -m streamlit run slik_checker/ui/app.py

run-scheduler:
	python -m slik_checker.cli run

init:
	python -m slik_checker.cli init
