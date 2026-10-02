#!/mnt/c/Users/rlwjd/OneDrive/Desktop/repo/slack-webhook/venv/bin/python
import json
import os
import re
import subprocess
import sys
import time
import psutil
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


CPU_THRESHOLD = 80.0
RAM_THRESHOLD = 85.0
CONTAINER_NAME_FILTER = "temis"


def load_webhook_url():
    env_path = Path(__file__).resolve().parent / ".env"
    webhook_url = None
    
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if "SLACK_WEBHOOK_URL" in line and "=" in line:
            webhook_url = line.split("=", 1)[1].strip().strip("'\"").removeprefix("export ").strip()
            break

    if not webhook_url:
        raise ValueError("SLACK_WEBHOOK_URL을 .env에서 찾을 수 없습니다.")

    return webhook_url



def get_cpu_usage():
    """CPU 사용률"""
    return psutil.cpu_percent(interval=1)


def get_ram_usage():
    """RAM 사용률"""
    return psutil.virtual_memory().percent



def get_container_issues():
    """비정상 컨테이너 반환"""
    result = subprocess.run(
        ["docker", "ps", "-a", "--format", "{{.Names}}\t{{.State}}\t{{.Status}}"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )

    issues = []
    for line in result.stdout.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue

        name, state, status = parts
        if CONTAINER_NAME_FILTER.lower() not in name.lower():
            continue

        health_match = re.search(r"\((healthy|unhealthy|starting)\)", status, re.IGNORECASE)
        health = health_match.group(1).lower() if health_match else None

        if state.lower() != "running":
            issues.append(f"{name}: {status}")
        elif health in ("unhealthy", "starting"):
            issues.append(f"{name}: health {health} ({status})")

    return issues


def check_my_server():
    """CPU, RAM, 대상 Docker 확인"""
    cpu_usage = ram_usage = None
    container_issues = []
    check_errors = []

    for check_func, error_msg in [
        (get_cpu_usage, "CPU 확인 실패"),
        (get_ram_usage, "RAM 확인 실패"),
        (get_container_issues, "Docker 확인 실패"),
    ]:
        try:
            result = check_func()
            if "CPU" in error_msg:
                cpu_usage = result
            elif "RAM" in error_msg:
                ram_usage = result
            else:
                container_issues = result
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            check_errors.append(f"{error_msg}: {exc}")

    return cpu_usage, ram_usage, container_issues, check_errors


def send_slack_alert(webhook_url, cpu, ram, container_issues, check_errors, alerts):
    fields = [
        {"title": "CPU 사용률", "value": f"{cpu:.1f}%" if cpu else "확인 실패", "short": True},
        {"title": "RAM 사용률", "value": f"{ram:.1f}%" if ram else "확인 실패", "short": True},
        {
            "title": f"비정상 컨테이너 ({CONTAINER_NAME_FILTER})",
            "value": "\n".join(container_issues) or "없음",
            "short": False,
        },
    ]

    if check_errors:
        fields.append({"title": "확인 오류", "value": "\n".join(check_errors), "short": False})

    if alerts:
        fields.append({"title": "알림", "value": "\n".join(alerts), "short": False})


    is_critical = bool(container_issues or check_errors or (cpu and cpu >= 90))
    payload = {
        "attachments": [{
            "color": "#ff0000" if is_critical else "#ff9900",
            "title": "[서버 모니터링] 이상 감지",
            "text": "우분투 서버 상태를 확인해 주세요.",
            "fields": fields,
        }]
    }

    request = Request(
        webhook_url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlopen(request, timeout=10) as response:
            response.read()
    except HTTPError as exc:
        raise RuntimeError(f"Slack이 HTTP {exc.code} 응답을 반환했습니다.") from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"Slack 전송에 실패했습니다: {exc}") from exc


def main():
    try:
        webhook_url = load_webhook_url()
        cpu, ram, container_issues, check_errors = check_my_server()

        alerts = list(check_errors)
        if cpu and cpu >= CPU_THRESHOLD:
            alerts.append(f"CPU 사용률이 높습니다: {cpu:.1f}% (기준 {CPU_THRESHOLD:.0f}%)")
        if ram and ram >= RAM_THRESHOLD:
            alerts.append(f"RAM 사용률이 높습니다: {ram:.1f}% (기준 {RAM_THRESHOLD:.0f}%)")
        if container_issues:
            alerts.append("비정상 컨테이너: " + ", ".join(container_issues))

        if not alerts:
            print("정상: 알림 기준을 초과한 항목이 없습니다.")
            return

        send_slack_alert(webhook_url, cpu, ram, container_issues, check_errors, alerts)
        print("Slack 알림을 전송했습니다.")

    except (OSError, ValueError, RuntimeError) as exc:
        print(f"모니터링 오류: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()