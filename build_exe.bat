@echo off
REM ShopTrack GH — one-click Windows build.
REM Run this from the shoptrack_gh folder.

echo === Installing build dependencies ===
pip install --upgrade pip
pip install -r requirements.txt
pip install pyinstaller

echo === Cleaning previous build ===
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo === Building ShopTrackGH.exe ===
pyinstaller build_shoptrack.spec --noconfirm

echo === Ensuring data/ + logs/ next to exe ===
if not exist dist\data mkdir dist\data
if not exist dist\data\backups mkdir dist\data\backups
if not exist dist\logs mkdir dist\logs

echo.
echo === DONE ===
echo Output: dist\ShopTrackGH.exe
echo Data:   dist\data\shoptrack.db (created on first run)
echo.
echo Test it: double-click dist\ShopTrackGH.exe
echo Then open http://127.0.0.1:5000 in your browser
pause