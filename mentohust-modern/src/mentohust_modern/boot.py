"""Packaged SYSTEM boot authentication installation and state."""
from __future__ import annotations
import base64
import ctypes
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from . import APP_VERSION
from .config import MentohustConfig
from .windows import hidden_subprocess_kwargs

TASK_NAME = "XinRuijie-Boot"
MARKER = "XinRuijie integrated boot authentication v1"


def runtime_root() -> Path:
    buffer = ctypes.create_unicode_buffer(32768)
    if ctypes.windll.shell32.SHGetFolderPathW(None, 35, None, 0, buffer) != 0:
        raise RuntimeError("无法读取系统应用数据目录。")
    return Path(buffer.value) / "XinRuijieBoot"


SCRIPT = r"""
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new()
$r=ConvertFrom-Json ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__PAYLOAD__')))
$root=$r.root
$name='XinRuijie-Boot'
$marker='XinRuijie integrated boot authentication v1'
$legacyName='XinRuijie-Boot-AutoConnect'
$all=@(Get-ScheduledTask -TaskPath '\')
function Owned($task,$description) {
    if($null -eq $task){return}
    $owner=$task.Principal.UserId
    if($owner -notmatch '^S-1-'){$owner=([Security.Principal.NTAccount]::new($owner)).Translate([Security.Principal.SecurityIdentifier]).Value}
    if($task.Description -ne $description -or $owner -ne 'S-1-5-18'){throw '任务名称已被其他程序占用。'}
}
$task=$all | Where-Object {$_.TaskName -eq $name}
$legacy=$all | Where-Object {$_.TaskName -eq $legacyName}
Owned $task $marker
Owned $legacy 'XinRuijie boot authentication v1'
if($task -and ($task.Actions.Count -ne 1 -or $task.Actions.Arguments -ne '--boot-worker' -or -not $task.Actions.Execute.StartsWith($root+'\',[StringComparison]::OrdinalIgnoreCase))){throw '后台任务路径异常。'}
if($legacy -and $legacy.Actions.Arguments -notlike '*XinRuijieAutoConnect\boot_auth.py"*'){throw '旧版后台任务路径异常。'}
if($r.operation -eq 'query'){
    @{enabled=[bool]($task -and $task.Settings.Enabled);legacy_enabled=[bool]($legacy -and $legacy.Settings.Enabled);executable=$(if($task){$task.Actions.Execute}else{''})} | ConvertTo-Json -Compress
    exit
}
function CheckRoot {
    if(-not (Test-Path -LiteralPath $root -PathType Container)){throw '后台目录不存在。'}
    $items=@(Get-Item -LiteralPath $root)+@(Get-ChildItem -LiteralPath $root -Recurse -Force)
    foreach($item in $items){
        if($item.Attributes -band [IO.FileAttributes]::ReparsePoint){throw '后台目录包含链接，操作已停止。'}
        $acl=Get-Acl -LiteralPath $item.FullName
        $owner=$acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
        if($owner -notin @('S-1-5-18','S-1-5-32-544')){throw '后台文件所有者异常。'}
        foreach($rule in $acl.Access){
            $sid=$rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
            if($rule.AccessControlType -eq 'Allow' -and $sid -notin @('S-1-5-18','S-1-5-32-544')){throw '后台目录权限异常。'}
        }
    }
}
if($r.operation -eq 'prepare'){
    if(Test-Path -LiteralPath $root){CheckRoot}
    else{
        New-Item -ItemType Directory -Path $root | Out-Null
        $acl=New-Object Security.AccessControl.DirectorySecurity
        $acl.SetAccessRuleProtection($true,$false)
        $acl.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))
        foreach($sid in 'S-1-5-18','S-1-5-32-544'){
            $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($sid),'FullControl','ContainerInherit,ObjectInherit','None','Allow'))
        }
        Set-Acl -LiteralPath $root -AclObject $acl
    }
    @{ok=$true}|ConvertTo-Json -Compress
    exit
}
CheckRoot
function StopWorker($t,$folder){
    if(-not $t){return}
    Disable-ScheduledTask -InputObject $t | Out-Null
    [IO.File]::WriteAllText((Join-Path $folder 'stop.request'),'stop')
    $until=(Get-Date).AddSeconds(15)
    do{
        Start-Sleep -Milliseconds 500
        $state=(Get-ScheduledTask -TaskName $t.TaskName -TaskPath '\').State
    }until($state -ne 'Running' -or (Get-Date) -gt $until)
    if($state -eq 'Running'){Stop-ScheduledTask -TaskName $t.TaskName -TaskPath '\'}
}
if($r.operation -eq 'snapshot'){
    $snapshot=@{task=$(if($task){Export-ScheduledTask -TaskName $name}else{''});legacy_enabled=[bool]($legacy -and $legacy.Settings.Enabled)}
    $snapshot | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $root 'rollback.json') -Encoding UTF8
}elseif($r.operation -eq 'preflight'){
    $checkName=$name+'-Check'
    $old=$all | Where-Object {$_.TaskName -eq $checkName}
    Owned $old $marker
    if($old){Unregister-ScheduledTask -TaskName $checkName -Confirm:$false}
    $principal=New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    $action=New-ScheduledTaskAction -Execute $r.exe -Argument '--boot-preflight' -WorkingDirectory $root
    $settings=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 2)
    Register-ScheduledTask -TaskName $checkName -Action $action -Principal $principal -Settings $settings -Description $marker | Out-Null
    Start-ScheduledTask -TaskName $checkName
}elseif($r.operation -eq 'clean_check'){
    $check=$all | Where-Object {$_.TaskName -eq ($name+'-Check')}
    Owned $check $marker
    if($check){Stop-ScheduledTask -InputObject $check;Unregister-ScheduledTask -InputObject $check -Confirm:$false}
}elseif($r.operation -eq 'stop'){
    StopWorker $task $root
    if($legacy -and $legacy.Settings.Enabled){StopWorker $legacy (Join-Path ([IO.Path]::GetDirectoryName($root)) 'XinRuijieAutoConnect')}
}elseif($r.operation -eq 'activate'){
    Remove-Item -LiteralPath (Join-Path $root 'stop.request') -Force -ErrorAction SilentlyContinue
    $principal=New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    $action=New-ScheduledTaskAction -Execute $r.exe -Argument '--boot-worker' -WorkingDirectory $root
    $boot=New-ScheduledTaskTrigger -AtStartup
    $boot.Delay='PT20S'
    $timer=New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)
    $settings=New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 20 -RestartInterval (New-TimeSpan -Minutes 1)
    Register-ScheduledTask -TaskName $name -Action $action -Principal $principal -Trigger @($boot,$timer) -Settings $settings -Description $marker -Force | Out-Null
    Start-ScheduledTask -TaskName $name
}elseif($r.operation -eq 'restore'){
    StopWorker $task $root
    $snapshot=Get-Content -LiteralPath (Join-Path $root 'rollback.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if($snapshot.task){
        Register-ScheduledTask -TaskName $name -Xml $snapshot.task -Force | Out-Null
        if((Get-ScheduledTask -TaskName $name).Settings.Enabled){
            Remove-Item -LiteralPath (Join-Path $root 'stop.request') -Force -ErrorAction SilentlyContinue
            Start-ScheduledTask -TaskName $name
        }
    }elseif($task){Unregister-ScheduledTask -InputObject $task -Confirm:$false}
    if($snapshot.legacy_enabled -and $legacy){
        Remove-Item -LiteralPath (Join-Path ([IO.Path]::GetDirectoryName($root)) 'XinRuijieAutoConnect\stop.request') -Force -ErrorAction SilentlyContinue
        Enable-ScheduledTask -InputObject $legacy | Out-Null
        Start-ScheduledTask -InputObject $legacy
    }
}else{throw '未知后台操作。'}
@{ok=$true}|ConvertTo-Json -Compress
"""


def _operation(operation: str, exe: Path | None = None) -> dict:
    payload = base64.b64encode(json.dumps({"operation": operation, "root": str(runtime_root()),
                                          "exe": str(exe or "")}).encode()).decode()
    script = SCRIPT.replace("__PAYLOAD__", payload)
    command = base64.b64encode(script.encode("utf-16-le")).decode()
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive",
                             "-EncodedCommand", command], capture_output=True,
                            encoding="utf-8", errors="replace", timeout=70,
                            **hidden_subprocess_kwargs())
    if result.returncode:
        raise RuntimeError("后台设置操作失败，请检查管理员权限及任务、目录归属。")
    try:
        data = json.loads(result.stdout)
    except ValueError as exc:
        raise RuntimeError("后台设置返回了无效结果。") from exc
    if operation == "query":
        if any(type(data.get(key)) is not bool for key in ("enabled", "legacy_enabled")):
            raise RuntimeError("后台设置状态未知。")
    elif data.get("ok") is not True:
        raise RuntimeError("后台设置未完成。")
    return data


def boot_status() -> dict:
    return _operation("query")


def read_status() -> dict:
    try:
        data = json.loads((runtime_root() / "status.json").read_text(encoding="utf-8"))
        data["fresh"] = 0 <= time.time() - float(data["updated_at"]) < 40
        return data
    except (OSError, ValueError, KeyError, TypeError):
        return {"fresh": False, "authenticated": False, "state": "等待后台启动"}


def _await_report(path: Path, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            time.sleep(1)
    raise RuntimeError("系统账户预检超时，原连接设置已保留。")


def set_boot_enabled(enabled: bool, config: MentohustConfig | None = None) -> dict:
    from .process_lock import exclusive
    with exclusive("Global\\XinRuijieBootInstallV1"):
        return _set_boot_enabled(enabled, config)


def _set_boot_enabled(enabled: bool, config: MentohustConfig | None = None) -> dict:
    from .elevation import is_admin
    if not getattr(sys, "frozen", False):
        raise RuntimeError("请使用打包后的新锐捷设置开机自动联网。")
    if not is_admin():
        raise RuntimeError("请以管理员身份运行新锐捷。")
    before = boot_status()
    if not enabled:
        _operation("prepare")
        _operation("stop")
        result = boot_status()
        if result["enabled"] or result["legacy_enabled"]:
            raise RuntimeError("开机自动联网尚未关闭。")
        return result
    from .runtime_checks import validation_errors
    if config is None:
        raise ValueError("请先填写校园网账号、密码并选择网卡。")
    errors = validation_errors(config)
    if errors:
        raise ValueError(errors[0])
    _operation("prepare")
    root = runtime_root()
    source = Path(sys.executable)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    target = root / "versions" / (APP_VERSION + "-" + digest[:12]) / "XinRuijie.exe"
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() != target.resolve():
        if not target.exists():
            shutil.copyfile(source, target)
        if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise RuntimeError("后台程序校验失败。")
    candidate = root / "candidate.json"
    replace(config, remember_password=True, auto_connect=True).save_json(candidate, machine=True)
    check_path = root / "preflight.json"
    check_path.unlink(missing_ok=True)
    try:
        _operation("preflight", target)
        check = _await_report(check_path, 90)
        if check.get("ok") is not True or check.get("version") != APP_VERSION:
            raise RuntimeError("系统账户预检失败，请检查网卡、密码和抓包驱动。")
    finally:
        _operation("clean_check")
    profile = root / "profile.json"
    previous = profile.read_bytes() if profile.exists() else None
    _operation("snapshot")
    try:
        _operation("stop")
        os.replace(candidate, profile)
        (root / "status.json").unlink(missing_ok=True)
        _operation("activate", target)
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            state = read_status()
            if state.get("fresh") and state.get("authenticated") and state.get("state") in ("在线保持", "等待心跳"):
                result = boot_status()
                if not result["enabled"] or result["legacy_enabled"]:
                    raise RuntimeError("后台任务状态校验失败。")
                return result
            time.sleep(2)
        raise RuntimeError("后台认证未成功，已恢复原有启动设置。")
    except Exception:
        _operation("stop")
        if previous is None:
            profile.unlink(missing_ok=True)
        else:
            profile.write_bytes(previous)
        _operation("restore")
        raise
