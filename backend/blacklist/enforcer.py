"""
Enforcer - 进程强制终止器
参考 ExecTool 的安全守卫模式：
  - 只允许杀黑名单里的进程（白名单守卫）
  - 系统保护名单不可杀（防误杀）
  - 用 taskkill 执行，不依赖第三方库
"""
import logging
import subprocess

logger = logging.getLogger(__name__)

# 系统保护名单（永远不允许杀）
# 参考 shell 工具的 deny_patterns 思路
_SYSTEM_PROTECTED = {
    "system", "system idle process", "registry", "smss.exe",
    "csrss.exe", "wininit.exe", "winlogon.exe", "lsass.exe",
    "services.exe", "svchost.exe", "explorer.exe", "dwm.exe",
    "taskmgr.exe", "ntoskrnl.exe", "python.exe", "pythonw.exe",
    "navi.exe",   # 不能杀自己
}


def _notify(process_name: str):
    """发送 Windows 系统通知"""
    try:
        # 优先用 win10toast（轻量）
        from win10toast import ToastNotifier
        ToastNotifier().show_toast(
            "Navi 🔒 学习模式",
            f"{process_name} 已被关闭，专注！",
            duration=4,
            threaded=True,
        )
    except ImportError:
        # 备用：用 PowerShell 弹 BalloonTip（无需第三方库）
        try:
            script = (
                f"Add-Type -AssemblyName System.Windows.Forms; "
                f"$n = New-Object System.Windows.Forms.NotifyIcon; "
                f"$n.Icon = [System.Drawing.SystemIcons]::Information; "
                f"$n.Visible = $true; "
                f"$n.ShowBalloonTip(4000, 'Navi 🔒 学习模式', '{process_name} 已被关闭，专注！', "
                f"[System.Windows.Forms.ToolTipIcon]::Info)"
            )
            subprocess.Popen(
                ["powershell", "-WindowStyle", "Hidden", "-Command", script],
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except Exception as e:
            logger.debug(f"[Enforcer] 通知发送失败: {e}")


def kill_process(process_name: str) -> tuple[bool, str]:
    """
    强制终止指定进程。

    Returns:
        (success, message)
    """
    name_lower = process_name.lower().strip()

    # 安全守卫：系统保护进程不可杀
    if name_lower in _SYSTEM_PROTECTED:
        msg = f"[Enforcer] 拒绝：{process_name} 在系统保护名单中"
        logger.warning(msg)
        return False, msg

    # 安全守卫：进程名不能为空或含路径分隔符（防注入）
    if not name_lower or "/" in name_lower or "\\" in name_lower:
        msg = f"[Enforcer] 拒绝：非法进程名 {process_name!r}"
        logger.warning(msg)
        return False, msg

    try:
        # /f 强制, /im 按进程名
        result = subprocess.run(
            ["taskkill", "/f", "/im", process_name],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            msg = f"[Enforcer] 已终止进程：{process_name}"
            logger.info(msg)
            _notify(process_name)
            return True, msg
        else:
            # 128 = 进程不存在（已退出），不算错误
            if result.returncode == 128:
                return True, f"[Enforcer] {process_name} 已不在运行"
            msg = f"[Enforcer] taskkill 失败({result.returncode}): {result.stderr.strip()}"
            logger.warning(msg)
            return False, msg
    except subprocess.TimeoutExpired:
        return False, f"[Enforcer] taskkill 超时：{process_name}"
    except Exception as e:
        return False, f"[Enforcer] 异常：{e}"
