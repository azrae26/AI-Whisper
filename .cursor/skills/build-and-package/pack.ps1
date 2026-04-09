# 功能：純打包（不推 git）
# 用法：powershell -ExecutionPolicy Bypass -File ".cursor/skills/build-and-package/pack.ps1"

$workspace = "f:\Cursor\AI Whisper"
$script = "$workspace\.cursor\skills\build-and-package\deploy.ps1"

# 終止舊程序
taskkill /F /IM "AI Whisper.exe"
taskkill /F /IM python.exe

# 依序執行 build 再 zip
& powershell -ExecutionPolicy Bypass -File $script -Role build
& powershell -ExecutionPolicy Bypass -File $script -Role zip
