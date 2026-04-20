@echo off
setlocal ENABLEDELAYEDEXPANSION
chcp 65001 >nul

cd /d %~dp0

echo ======================================
echo   Telegram Keyword Monitor Bot
 echo ======================================

if not exist ".venv\Scripts\python.exe" (
  echo [1/4] Создаю виртуальное окружение...
  py -3 -m venv .venv
  if errorlevel 1 (
    echo Не удалось создать виртуальное окружение. Установите Python 3.11+.
    pause
    exit /b 1
  )
)

echo [2/4] Обновляю pip...
call .venv\Scripts\python.exe -m pip install --upgrade pip >nul

echo [3/4] Устанавливаю зависимости...
call .venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 (
  echo Не удалось установить зависимости.
  pause
  exit /b 1
)

if not exist ".env" (
  echo [4/4] Первый запуск: нужен BOT_TOKEN.
  set /p BOT_TOKEN=Вставьте BOT_TOKEN от @BotFather и нажмите Enter: 
  if "!BOT_TOKEN!"=="" (
    echo BOT_TOKEN пустой. Запуск отменен.
    pause
    exit /b 1
  )
  > .env echo BOT_TOKEN=!BOT_TOKEN!
  echo Файл .env создан.
) else (
  echo [4/4] Файл .env уже найден.
)

echo.
echo Запускаю бота...
call .venv\Scripts\python.exe bot.py

echo.
echo Бот остановлен.
pause
