.PHONY: dev install test

# Start the dev server with auto-reload on 0.0.0.0:8000.
dev:
	.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Create the venv and install pinned dependencies.
install:
	python -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -r requirements.txt

# Run the test suite.
test:
	.venv/bin/pytest -q
