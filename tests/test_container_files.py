import json
from pathlib import Path
import re


PROJECT_ROOT = Path(__file__).resolve().parents[1]



def test_dockerfile_defines_non_root_production_runtime() -> None:
    dockerfile_path = PROJECT_ROOT / "Dockerfile"
    assert dockerfile_path.is_file(), "Dockerfile must exist at the project root."

    content = dockerfile_path.read_text(encoding="utf-8")
    instructions = [
        line.strip()
        for line in content.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    from_instructions = [
        line for line in instructions if line.startswith("FROM ")
    ]
    python_stage = next(
        (line for line in from_instructions if "python:" in line),
        "",
    )
    image_match = re.search(
        r"FROM python:(\d+)\.(\d+)-slim(?:-[a-z0-9]+)?",
        python_stage,
    )
    assert image_match is not None
    assert tuple(map(int, image_match.groups())) >= (3, 11)

    assert "COPY requirements.txt ./" in instructions
    assert (
        "RUN python -m pip install --no-cache-dir -r requirements.txt"
        in instructions
    )
    assert "COPY --chown=app:app app ./app" in instructions
    assert "COPY --chown=app:app alembic.ini ./" in instructions
    assert "COPY --chown=app:app migrations ./migrations" in instructions
    assert "COPY . ." not in instructions

    user_index = instructions.index("USER app")
    command_index = next(
        index
        for index, instruction in enumerate(instructions)
        if instruction.startswith("CMD ")
    )
    assert user_index < command_index

    command = json.loads(instructions[command_index].removeprefix("CMD "))
    assert command[:2] == ["sh", "-c"]
    assert command[2].startswith("python -m alembic upgrade head && exec uvicorn ")
    assert "app.main:app" in command[2]
    assert "--host 0.0.0.0" in command[2]
    assert "--port 8000" in command[2]
    assert "--no-access-log" in command[2]
    assert "--reload" not in content


def test_dockerfile_builds_frontend_and_serves_it_via_caddy() -> None:
    """前端产物在镜像构建期生成，并由 Caddy 目标阶段同源托管。"""
    dockerfile_path = PROJECT_ROOT / "Dockerfile"
    content = dockerfile_path.read_text(encoding="utf-8")
    instructions = [
        line.strip()
        for line in content.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    from_instructions = [
        line for line in instructions if line.startswith("FROM ")
    ]

    # 前端构建阶段：安装依赖并产出 frontend/dist。
    node_stage = next(
        (line for line in from_instructions if "node:" in line),
        "",
    )
    assert node_stage, "Dockerfile must include a Node.js frontend build stage."
    node_image_match = re.search(r"node:(\d+)", node_stage)
    assert node_image_match is not None
    assert int(node_image_match.group(1)) >= 20
    assert any(
        instruction.startswith("RUN npm ci") for instruction in instructions
    )
    assert "RUN npm run build" in instructions

    # Web 阶段：Caddy 携带配置与前端产物。
    web_stage = next(
        (line for line in from_instructions if "caddy:" in line),
        "",
    )
    assert web_stage, "Dockerfile must include a Caddy web stage."
    assert "COPY Caddyfile /etc/caddy/Caddyfile" in instructions
    assert any(
        instruction.endswith("/srv/frontend")
        and "dist" in instruction
        and instruction.startswith("COPY --from=")
        for instruction in instructions
    )


def test_dockerignore_excludes_secrets_and_local_artifacts() -> None:
    dockerignore_path = PROJECT_ROOT / ".dockerignore"
    assert dockerignore_path.is_file(), (
        ".dockerignore must exist at the project root."
    )

    patterns = {
        line.strip()
        for line in dockerignore_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert {
        ".git/",
        ".venv/",
        ".env*",
        "data/",
        "**/__pycache__/",
        "**/*.py[cod]",
        ".pytest_cache/",
        ".pytest-tmp/",
        "tests/",
    } <= patterns


def test_compose_defines_private_app_and_isolated_caddy() -> None:
    compose_path = PROJECT_ROOT / "docker-compose.yml"
    assert compose_path.is_file(), (
        "docker-compose.yml must exist at the project root."
    )

    content = compose_path.read_text(encoding="utf-8")
    assert "app:" in content
    assert "caddy:" in content
    assert "target: app" in content
    assert "target: web" in content
    assert "env_file:" in content
    assert "- .env" in content
    assert "${DATA_HOST_DIR:-./data}:/app/data" in content
    assert 'expose:\n      - "8000"' in content
    assert "8000:8000" not in content
    assert "DOMAIN: ${DOMAIN:?Set DOMAIN in .env}" in content
    assert "caddy_data:/data" in content
    assert "caddy_config:/config" in content
    assert content.count("restart: unless-stopped") == 2
    assert 'max-size: "10m"' in content
    assert 'max-file: "3"' in content


def test_caddyfile_terminates_https_for_the_configured_domain() -> None:
    caddyfile_path = PROJECT_ROOT / "Caddyfile"
    assert caddyfile_path.is_file(), (
        "Caddyfile must exist at the project root."
    )

    content = caddyfile_path.read_text(encoding="utf-8")
    assert "{$DOMAIN}" in content
    assert "reverse_proxy" in content
    assert "app:8000" in content
    assert "http://" not in content

    # 前端同源托管：静态根目录、SPA 回退，以及 API 路径转发。
    assert "root * /srv/frontend" in content
    assert "try_files {path} /index.html" in content
    assert "/api/*" in content
    assert "/health" in content
    assert "/openapi.json" in content


def test_deployment_runbook_covers_required_operations() -> None:
    runbook_path = PROJECT_ROOT / "docs" / "deployment.md"
    assert runbook_path.is_file(), (
        "docs/deployment.md must provide the production runbook."
    )

    content = runbook_path.read_text(encoding="utf-8")
    required_fragments = {
        "docker compose config --quiet",
        "docker compose up -d --build",
        "docker compose up -d --force-recreate app",
        "docker compose logs",
        "sqlite3",
        ".backup",
        "\\%F",
        "-mtime +30",
        "PRAGMA integrity_check",
        "docker compose down -v",
        "DATA_HOST_DIR",
        "BACKUP_HOST_DIR",
        "APP_API_TOKEN",
        "ADMIN_PASSWORD",
        "python -m alembic upgrade head",
        "A/AAAA",
        "80/443",
        "SSH",
    }
    missing = sorted(
        fragment for fragment in required_fragments if fragment not in content
    )
    assert not missing, f"Runbook is missing required operations: {missing}"

    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs/deployment.md" in readme
