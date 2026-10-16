"""Current-user logon startup; no account password is stored in the task."""
from __future__ import annotations

import base64
import json
from pathlib import Path
import subprocess
import sys

from .windows import hidden_subprocess_kwargs


SCRIPT = r"""
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new()
$request=ConvertFrom-Json ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__PAYLOAD__')))
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$name='XinRuijie-Logon-'+$sid
$marker='XinRuijie current-user logon startup v1'
# Enumerating this folder distinguishes a missing task from a query failure.
$task=@(Get-ScheduledTask -TaskPath '\' | Where-Object {$_.TaskName -eq $name})
if($task.Count -gt 1){throw 'Unexpected task count'}
if($task.Count -eq 1){
    $task=$task[0]
    $owner=$task.Principal.UserId
    if($owner -notmatch '^S-1-'){
        $owner=([Security.Principal.NTAccount]::new($owner)).Translate([Security.Principal.SecurityIdentifier]).Value
    }
    if($task.Description -ne $marker -or $owner -ne $sid){
        throw 'A different task already uses this startup name'
    }
}else{$task=$null}
if($request.operation -eq 'enable'){
    if(-not (Test-Path -LiteralPath $request.exe -PathType Leaf)){throw 'Executable not found'}
    $action=New-ScheduledTaskAction -Execute $request.exe -WorkingDirectory ([IO.Path]::GetDirectoryName($request.exe))
    $trigger=New-ScheduledTaskTrigger -AtLogOn -User $sid
    $trigger.Delay='PT10S'
    $principal=New-ScheduledTaskPrincipal -UserId $sid -LogonType Interactive -RunLevel Highest
    $settings=New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
    Register-ScheduledTask -TaskName $name -TaskPath '\' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description $marker -Force | Out-Null
    $task=Get-ScheduledTask -TaskName $name -TaskPath '\'
    if(-not $task.Settings.Enabled -or $task.Actions.Execute -ne $request.exe){throw 'Startup verification failed'}
}elseif($request.operation -eq 'disable'){
    if($null -ne $task){Unregister-ScheduledTask -TaskName $name -TaskPath '\' -Confirm:$false}
    if(@(Get-ScheduledTask -TaskPath '\' | Where-Object {$_.TaskName -eq $name}).Count -ne 0){throw 'Startup removal verification failed'}
    $task=$null
}elseif($request.operation -ne 'query'){throw 'Unsupported operation'}
@{enabled=($null -ne $task -and $task.Settings.Enabled); executable=$(if($null -ne $task){$task.Actions.Execute}else{''})} | ConvertTo-Json -Compress
"""


def startup_status() -> dict:
    return _operation("query")


def set_startup(enabled: bool) -> dict:
    if not getattr(sys, "frozen", False):
        raise RuntimeError("请在打包后的新锐捷程序中设置开机自启动。")
    return _operation("enable" if enabled else "disable", Path(sys.executable).resolve())


def _operation(operation: str, executable: Path | None = None) -> dict:
    payload = base64.b64encode(json.dumps({"operation": operation, "exe": str(executable or "")}, ensure_ascii=False).encode("utf-8")).decode("ascii")
    script = SCRIPT.replace("__PAYLOAD__", payload)
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True, encoding="utf-8", errors="replace", timeout=30, **hidden_subprocess_kwargs())
    if result.returncode:
        raise RuntimeError("启动设置操作失败。请以管理员身份运行新锐捷，并确认 Windows 任务计划程序可用。")
    data = json.loads(result.stdout.lstrip("\ufeff").strip())
    if not isinstance(data, dict) or type(data.get("enabled")) is not bool:
        raise RuntimeError("无法确认 Windows 启动设置的状态。")
    return data
