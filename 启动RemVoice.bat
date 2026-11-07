@echo off
cd /d "%~dp0"
rem Launch through cmd's CreateProcess path; this usually bypasses the
rem SmartScreen hook that Explorer uses for an unsigned exe.
rem If Windows still asks, choose "More info" -> "Run anyway".
start "" "%~dp0RemVoice.exe" %*
