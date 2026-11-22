@echo off
rem ============================================================
rem  Darksiders 2 local server - start the emulator
rem  Keep this window open while playing.
rem ============================================================
cd /d "%~dp0"
set DS2_EMPTY_DOC=1
set DS2_DOC_MAP=1
set DS2_GET_MAP=1
set DS2_DOC_SLOT=1
py Server\server\ds2_server.py
pause
