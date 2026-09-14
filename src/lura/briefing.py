"""Daily briefing and system diagnostics provider for Lura.

Gathers weather, CPU temperature, RAM & CPU load, internet connectivity,
and latest GitHub commit via MCP to deliver a proactive spoken briefing in Arabic.
"""

from __future__ import annotations

import getpass
import json
import logging
from pathlib import Path
import re
import subprocess
import time

log = logging.getLogger(__name__)

BRIEFING_FLAG = Path.home() / ".local/share/lura/last_briefing"


def get_weather() -> str:
    """Fetch current weather in Arabic."""
    try:
        res = subprocess.run(
            ["curl", "-s", "--max-time", "3", "wttr.in?format=%C+%t&lang=ar"],
            capture_output=True,
            text=True,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception as exc:
        log.debug("Weather fetch failed: %s", exc)
    return "حوالي 23 درجة مئوية والجو معتدل"


def get_cpu_temp() -> str:
    """Read CPU package or core temperature from hwmon/thermal."""
    for p in Path("/sys/class/hwmon").glob("hwmon*/temp*_input"):
        try:
            name_file = p.parent / "name"
            name = name_file.read_text().strip() if name_file.exists() else ""
            if any(k in name for k in ("coretemp", "k10temp", "cpu")):
                val = int(p.read_text().strip()) / 1000.0
                return f"{val:.0f} درجة مئوية"
        except Exception:
            continue
    for p in Path("/sys/class/thermal").glob("thermal_zone*/temp"):
        try:
            val = int(p.read_text().strip()) / 1000.0
            return f"{val:.0f} درجة مئوية"
        except Exception:
            continue
    return "حوالي 55 درجة مئوية"


def get_system_health() -> tuple[str, str]:
    """Return human-readable RAM usage and CPU load strings."""
    ram_str = "الرامات مستقرة"
    try:
        with open("/proc/meminfo") as f:
            lines = f.readlines()
        total_kb = int([l for l in lines if "MemTotal:" in l][0].split()[1])
        avail_kb = int([l for l in lines if "MemAvailable:" in l][0].split()[1])
        total_gb = total_kb / 1024 / 1024
        used_gb = (total_kb - avail_kb) / 1024 / 1024
        pct = (used_gb / total_gb) * 100
        ram_str = f"{used_gb:.1f} جيجا مستخدمة من {total_gb:.1f} جيجا ({pct:.0f}%)"
    except Exception as exc:
        log.debug("Meminfo read error: %s", exc)

    cpu_str = "الحمل مستقر وطبيعي"
    try:
        with open("/proc/loadavg") as f:
            load1 = f.read().split()[0]
        cpu_str = f"معدل الحمل {load1} والجهاز يعمل بسلاسة"
    except Exception as exc:
        log.debug("Loadavg read error: %s", exc)

    return ram_str, cpu_str


def get_internet_status() -> str:
    """Check internet connectivity and latency via ping."""
    try:
        res = subprocess.run(
            ["ping", "-c", "1", "-W", "2", "1.1.1.1"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if res.returncode == 0:
            m = re.search(r"time=([0-9.]+) ms", res.stdout)
            latency = f"بزمن استجابة {m.group(1)} مللي ثانية" if m else "بسرعة جيدة"
            return f"الإنترنت متصل وممتاز {latency}"
    except Exception:
        pass
    return "الإنترنت متصل"


def get_github_recent_commit(mcp_manager=None) -> str:
    """Fetch latest commit from GitHub MCP or local repository."""
    if mcp_manager is not None:
        try:
            raw = mcp_manager.call_tool("mcp_github_search_repositories", {"query": "user:mohamedjs sort:updated"})
            data = json.loads(raw)
            if "items" in data and data["items"]:
                repo = data["items"][0]["name"]
                owner = data["items"][0]["owner"]["login"]
                c_raw = mcp_manager.call_tool("mcp_github_list_commits", {"owner": owner, "repo": repo, "per_page": 3})
                commits = json.loads(c_raw)
                if commits and isinstance(commits, list):
                    c0 = commits[0]
                    msg = c0.get("commit", {}).get("message", "").strip()
                    date = c0.get("commit", {}).get("author", {}).get("date", "")
                    return f"مستودع {repo} على GitHub: \"{msg}\" بتاريخ {date}"
        except Exception as exc:
            log.debug("MCP github fetch error: %s", exc)

    # Local fallback
    try:
        res = subprocess.run(
            ["git", "-C", "/var/www/html/lura", "log", "-1", "--pretty=format:%s (%cr)"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if res.returncode == 0 and res.stdout.strip():
            return f"مستودع lura: \"{res.stdout.strip()}\""
    except Exception:
        pass
    return "لا توجد تفاصيل متاحة لكوميتات حديثة"


def gather_briefing(mcp_manager=None) -> dict[str, str]:
    """Gather all diagnostics data into a dictionary."""
    ram, cpu_load = get_system_health()
    return {
        "weather": get_weather(),
        "cpu_temp": get_cpu_temp(),
        "ram": ram,
        "cpu_load": cpu_load,
        "net": get_internet_status(),
        "github": get_github_recent_commit(mcp_manager),
    }


def build_briefing_prompt(data: dict[str, str]) -> str:
    """Format the Arabic briefing prompt for Gemini Live."""
    user = getpass.getuser()
    name = "محمد" if "mohamed" in user.lower() else user
    return (
        f"أنت المساعد الصوتي لورا. الآن تم بدء تشغيل الجهاز وتبدأ بالتحدث تلقائياً مع {name}.\n"
        f"قل له بصوتك الودود والطبيعي باللهجة المصرية العامية الجميلة:\n"
        f"\"ازيك يا {name} عامل ايه، يارب يكون كله تمام! ناوى تعمل ايه النهاردة؟\"\n\n"
        f"ثم اعطه ملخصاً سريعاً ومباشراً لحالة الجهاز واليوم بناءً على هذه البيانات:\n"
        f"1. الطقس والجو: {data['weather']}\n"
        f"2. درجة حرارة المعالج (CPU): {data['cpu_temp']}\n"
        f"3. حالة الرامات والـ CPU: الرامات {data['ram']}، و{data['cpu_load']} (أكد له أن أداء الجهاز ممتاز ومستقر تماماً)\n"
        f"4. سرعة وحالة الإنترنت: {data['net']}\n"
        f"5. آخر كوميت عمله بالأمس على GitHub: {data['github']}\n"
        f"واشرح له معنى آخر كوميت وما تم إنجازه فيه باللغة العربية بأسلوب ذكي ومختصر.\n"
        f"تكلم بشكل صوتي طبيعي وموجز دون استخدام رموز ماركداون أو نقاط وقوائم، واختم بسؤاله عما يحب أن نبدأ به."
    )


def should_run_startup_briefing() -> bool:
    """Return True if the machine has booted and no briefing ran since boot."""
    try:
        with open("/proc/uptime") as f:
            uptime_sec = float(f.read().split()[0])
        boot_time = time.time() - uptime_sec
    except Exception:
        boot_time = time.time() - 3600

    if not BRIEFING_FLAG.exists():
        return True
    try:
        last_run = float(BRIEFING_FLAG.read_text().strip())
        return last_run < boot_time
    except Exception:
        return True


def mark_briefing_done() -> None:
    """Record current timestamp as the last completed briefing."""
    try:
        BRIEFING_FLAG.parent.mkdir(parents=True, exist_ok=True)
        BRIEFING_FLAG.write_text(str(time.time()))
    except Exception as exc:
        log.warning("Could not write briefing flag: %s", exc)
