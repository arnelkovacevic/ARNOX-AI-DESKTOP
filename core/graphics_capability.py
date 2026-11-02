"""Static device-capability detection for ARNOX graphics Auto mode."""

from __future__ import annotations

import hashlib
import json
import platform
import re
import subprocess
from dataclasses import asdict, dataclass

import psutil


@dataclass(frozen=True)
class GraphicsCapability:
    platform: str
    physical_cores: int
    logical_cores: int
    ram_gb: float
    gpu_name: str
    vram_mb: int | None
    gpu_class: str
    quality: str
    reason: str
    fingerprint: str

    def to_dict(self) -> dict:
        return asdict(self)


def _run(command: list[str], timeout: float = 3.0) -> str:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return result.stdout if result.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _classify_gpu(name: str) -> str:
    lowered = str(name or "").lower()
    if not lowered or lowered == "gpu unavailable":
        return "unknown"
    if any(marker in lowered for marker in (
        "software", "llvmpipe", "swiftshader", "microsoft basic", "virtualbox",
    )):
        return "software"
    if re.search(r"\bapple\s+m\d", lowered):
        return "apple_silicon"
    if any(marker in lowered for marker in (
        "nvidia", "geforce", "quadro", "tesla", "radeon pro", "radeon rx",
        "radeon vii", "arc a", "arc b",
    )):
        return "discrete"
    if any(marker in lowered for marker in (
        "intel", "iris", "uhd", "hd graphics", "vega", "integrated",
    )):
        return "integrated"
    return "unknown"


def score_graphics_capability(
    *,
    system: str,
    physical_cores: int,
    logical_cores: int,
    ram_gb: float,
    gpu_name: str,
    vram_mb: int | None,
) -> GraphicsCapability:
    """Resolve Low/Medium/High from stable hardware, never runtime load."""
    physical_cores = max(1, int(physical_cores or 1))
    logical_cores = max(physical_cores, int(logical_cores or physical_cores))
    ram_gb = max(0.0, float(ram_gb or 0.0))
    gpu_name = str(gpu_name or "GPU unavailable").strip() or "GPU unavailable"
    vram_mb = int(vram_mb) if vram_mb is not None and int(vram_mb) > 0 else None
    gpu_class = _classify_gpu(gpu_name)

    score = 0
    if ram_gb < 8:
        score -= 2
    elif ram_gb >= 32:
        score += 2
    elif ram_gb >= 16:
        score += 1

    if physical_cores <= 2:
        score -= 2
    elif physical_cores >= 8:
        score += 2
    elif physical_cores >= 6:
        score += 1

    if gpu_class == "software":
        score -= 3
    elif gpu_class == "integrated" and ram_gb < 12:
        score -= 1
    elif gpu_class == "apple_silicon":
        score += 1
    elif gpu_class == "discrete":
        score += 2 if (vram_mb or 0) >= 4096 else 1

    high_allowed = (
        ram_gb >= 16
        and physical_cores >= 6
        and gpu_class in {"apple_silicon", "discrete"}
    )
    if score <= -2:
        quality = "low"
    elif score >= 4 and high_allowed:
        quality = "high"
    else:
        quality = "medium"

    gpu_summary = gpu_name
    if vram_mb:
        gpu_summary += f" · {vram_mb / 1024:.1f} GB VRAM"
    reason = f"{ram_gb:.0f} GB RAM · {physical_cores}-core CPU · {gpu_summary}"
    signature = json.dumps(
        {
            "system": system,
            "physical_cores": physical_cores,
            "logical_cores": logical_cores,
            "ram_gb": round(ram_gb, 1),
            "gpu_name": gpu_name.lower(),
            "vram_mb": vram_mb,
            "gpu_class": gpu_class,
        },
        sort_keys=True,
    )
    fingerprint = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:16]
    return GraphicsCapability(
        platform=system,
        physical_cores=physical_cores,
        logical_cores=logical_cores,
        ram_gb=round(ram_gb, 1),
        gpu_name=gpu_name,
        vram_mb=vram_mb,
        gpu_class=gpu_class,
        quality=quality,
        reason=reason,
        fingerprint=fingerprint,
    )


def _mac_gpu() -> tuple[str, int | None]:
    output = _run(["system_profiler", "SPDisplaysDataType"], timeout=4.0)
    name_match = re.search(r"Chipset Model:\s*(.+)", output)
    vram_match = re.search(
        r"VRAM(?: \([^)]*\))?:\s*([\d.]+)\s*(GB|MB)", output, re.IGNORECASE
    )
    name = name_match.group(1).strip() if name_match else "GPU unavailable"
    vram = None
    if vram_match:
        value = float(vram_match.group(1))
        vram = int(value * 1024 if vram_match.group(2).upper() == "GB" else value)
    return name, vram


def _windows_gpu() -> tuple[str, int | None]:
    script = (
        "Get-CimInstance Win32_VideoController | "
        "Select-Object Name,AdapterRAM | ConvertTo-Json -Compress"
    )
    output = _run(["powershell", "-NoProfile", "-Command", script], timeout=4.0)
    try:
        payload = json.loads(output)
        adapters = payload if isinstance(payload, list) else [payload]
        adapters = [adapter for adapter in adapters if isinstance(adapter, dict)]
        best = max(adapters, key=lambda item: int(item.get("AdapterRAM") or 0), default={})
        name = str(best.get("Name") or "GPU unavailable")
        raw_ram = int(best.get("AdapterRAM") or 0)
        return name, raw_ram // (1024 * 1024) if raw_ram else None
    except (TypeError, ValueError, json.JSONDecodeError):
        return "GPU unavailable", None


def _linux_gpu() -> tuple[str, int | None]:
    nvidia = _run([
        "nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"
    ])
    if nvidia.strip():
        first = nvidia.splitlines()[0]
        name, _, memory = first.partition(",")
        try:
            return name.strip(), int(float(memory.strip()))
        except ValueError:
            return name.strip(), None
    output = _run(["lspci"], timeout=2.0)
    for line in output.splitlines():
        if re.search(r"VGA|3D controller|Display controller", line, re.IGNORECASE):
            return line.split(":", 2)[-1].strip(), None
    return "GPU unavailable", None


def detect_graphics_capability() -> GraphicsCapability:
    system = platform.system() or "Unknown"
    physical = psutil.cpu_count(logical=False) or 1
    logical = psutil.cpu_count(logical=True) or physical
    ram_gb = psutil.virtual_memory().total / (1024 ** 3)
    if system == "Darwin":
        gpu_name, vram_mb = _mac_gpu()
    elif system == "Windows":
        gpu_name, vram_mb = _windows_gpu()
    elif system == "Linux":
        gpu_name, vram_mb = _linux_gpu()
    else:
        gpu_name, vram_mb = "GPU unavailable", None
    return score_graphics_capability(
        system=system,
        physical_cores=physical,
        logical_cores=logical,
        ram_gb=ram_gb,
        gpu_name=gpu_name,
        vram_mb=vram_mb,
    )
