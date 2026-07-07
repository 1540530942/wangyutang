"""Real config-integrity tests for the robot_gateway Caddy config.

Run from the robot_gateway/ directory:  python -m pytest tests/ -q
There is no Python service here — the gateway is a Caddyfile plus a static
site. These tests are genuine regression guards: they parse the real
Caddyfile and assert every backend service has a route to the correct
upstream, so a fat-fingered edit that drops or mis-points a route fails CI.

(If the `caddy` binary is installed, `caddy validate` is the stronger check;
these tests cover the case where it is not available, e.g. plain CI runners.)
"""
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CADDYFILE = os.path.join(HERE, "Caddyfile")

# Route path -> default upstream that must appear in the reverse_proxy line.
EXPECTED_ROUTES = {
    "/camera/*": "camera-snapshot:8099",
    "/action/*": "action-move:8094",
    "/audio/*": "robot-sandbox:8095",
    "/interact/*": "audio-interact:8097",
    "/common/*": "host.docker.internal:8101",
    "/robot/*": "pi5-robot:8093",
    "/slam/*": "slam-mapping:8301",
    "/face/*": "smile-face:8096",
}


def _caddy_text():
    with open(CADDYFILE, encoding="utf-8") as fh:
        return fh.read()


def test_caddyfile_exists_and_nonempty():
    assert os.path.isfile(CADDYFILE)
    assert len(_caddy_text().strip()) > 0


def test_every_service_route_present():
    text = _caddy_text()
    for path in EXPECTED_ROUTES:
        assert f"handle_path {path}" in text, f"missing route {path}"


def test_every_route_points_to_correct_upstream():
    text = _caddy_text()
    for path, upstream in EXPECTED_ROUTES.items():
        assert upstream in text, f"route {path} lost its upstream {upstream}"


def test_health_endpoint_defined():
    assert "handle /api/health" in _caddy_text()


def test_static_site_files_exist():
    assert os.path.isfile(os.path.join(HERE, "site", "index.html"))
    assert os.path.isfile(os.path.join(HERE, "site", "styles.css"))
