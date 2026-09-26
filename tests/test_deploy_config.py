"""Guards on the deploy config itself.

These are config tests, not runtime tests: they assert that the files Render
reads still say what we think they say. They catch a regression at commit time;
they cannot prove the deployed process actually did it. The behavioural
single-controller check runs against the live URL (see scripts/verify_live.py).
"""

from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
DOCKERFILE = (BACKEND / "Dockerfile").read_text(encoding="utf-8")


def _directives(path: Path) -> str:
    """Return a file's real directives, with comments and blanks stripped.

    Asserting against raw text would match our own explanatory comments — the
    prose in render.yaml mentions both "dockerCommand" and "LIVE" precisely
    because they must NOT appear as settings.

    ponytail: naive '#' split; fine because no value here contains a '#'.
    Switch to a YAML parse if that ever stops being true.
    """
    lines = (ln.split("#", 1)[0].rstrip() for ln in path.read_text(encoding="utf-8").splitlines())
    return "\n".join(ln for ln in lines if ln.strip())


RENDER_YAML = _directives(BACKEND / "render.yaml")


def _cmd_line() -> str:
    """Return the Dockerfile's CMD line."""
    lines = [ln for ln in DOCKERFILE.splitlines() if ln.startswith("CMD")]
    assert len(lines) == 1, f"expected exactly one CMD, found {len(lines)}"
    return lines[0]


def test_cmd_binds_all_interfaces():
    """Render requires the server to bind 0.0.0.0, not 127.0.0.1."""
    assert "--host 0.0.0.0" in _cmd_line()


def test_cmd_uses_injected_port_with_shell_expansion():
    """The port must come from $PORT at runtime, with a local fallback.

    Shell (not exec) form is required for ${PORT} to expand at all.
    """
    cmd = _cmd_line()
    assert "${PORT:-8000}" in cmd
    assert cmd.startswith('CMD ["sh", "-c"'), "exec form would not expand ${PORT}"


def test_cmd_pins_single_worker():
    """More than one worker means more than one controller fighting the fleet.

    Explicit --workers 1 also overrides uvicorn's $WEB_CONCURRENCY fallback
    (uvicorn/config.py: `if workers is None and "WEB_CONCURRENCY" in os.environ`).
    """
    assert "--workers 1" in _cmd_line()


def test_render_yaml_defines_no_docker_command():
    """The Dockerfile CMD must stay the single source of truth for startup.

    Render's blueprint spec does not document shell expansion for dockerCommand,
    so a "$PORT" there could reach uvicorn as a literal string.
    """
    assert "dockerCommand" not in RENDER_YAML


def test_render_yaml_pins_simulation_mode():
    """A deployed instance must never boot into LIVE/RealPlc."""
    assert "key: MODE" in RENDER_YAML
    assert "value: SIMULATION" in RENDER_YAML
    assert "LIVE" not in RENDER_YAML


def test_render_yaml_wires_the_health_check():
    """Render's health check must point at the endpoint app/main.py serves."""
    assert "healthCheckPath: /health" in RENDER_YAML


def test_cors_grants_no_standing_wildcard():
    """CORS stays scoped to localhost; the dashboard is served same-origin.

    allow_credentials=True is on (app/main.py), so a wildcard third-party host
    in the origin regex would be a standing credentialed grant.
    """
    from app.config import Settings

    regex = Settings().allowed_origin_regex
    assert "lovable" not in regex
    assert "github.dev" not in regex
    assert "localhost" in regex
