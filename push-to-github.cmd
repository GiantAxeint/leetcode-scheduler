@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在推送到 https://github.com/GiantAxeint/leetcode-scheduler ...
git push -u origin master
echo.
echo 看到 master -> master 就是成功了，按任意键关闭。
pause >nul
