# 领用申请门户 / 后台 看门狗脚本
# 由"登录时计划任务"自动启动；常驻循环，进程挂了自动拉起。
# 端口：门户=5001(apply_app.py)  后台=5000(app.py)

$ErrorActionPreference = 'SilentlyContinue'
$py  = 'C:\Users\32077\AppData\Local\Python\pythoncore-3.14-64\python.exe'
$dir = 'C:\chat_code\code_1'
$log = Join-Path $dir 'serve.log'

function Test-Port($port) {
    return [bool](Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)
}

function Start-App($script, $port) {
    if (-not (Test-Port $port)) {
        Start-Process -FilePath $py -ArgumentList $script -WorkingDirectory $dir -WindowStyle Hidden
        "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  started $script (port $port)" | Out-File -FilePath $log -Append -Encoding utf8
    }
}

while ($true) {
    Start-App 'apply_app.py' 5001   # 门户
    Start-App 'app.py'       5000   # 后台
    Start-Sleep -Seconds 30
}
