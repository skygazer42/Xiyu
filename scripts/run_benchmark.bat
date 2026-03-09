@echo off
REM ASR Benchmark 运行脚本 (Windows)
REM 使用方法:
REM   run_benchmark.bat                     # 默认测试 paraformer + onnx
REM   run_benchmark.bat all                 # 测试所有模型
REM   run_benchmark.bat prepare             # 准备测试数据
REM   run_benchmark.bat nano                # 仅测试 Fun-ASR-Nano

setlocal enabledelayedexpansion

set COMPOSE_FILE=docker/compose/legacy/docker-compose.benchmark.yml
set BENCHMARK_DIR=data\benchmark

REM 确保目录存在
if not exist "%BENCHMARK_DIR%" mkdir "%BENCHMARK_DIR%"

REM 解析参数
set CMD=%1
if "%CMD%"=="" set CMD=default

if "%CMD%"=="prepare" goto prepare
if "%CMD%"=="all" goto all
if "%CMD%"=="nano" goto nano
if "%CMD%"=="sensevoice" goto sensevoice
if "%CMD%"=="onnx" goto onnx
if "%CMD%"=="paraformer" goto paraformer
if "%CMD%"=="build" goto build
if "%CMD%"=="default" goto default
goto usage

:prepare
echo === 准备测试数据 ===
docker compose -f %COMPOSE_FILE% build
docker compose -f %COMPOSE_FILE% run --rm benchmark python scripts/prepare_benchmark_data.py
goto end

:build
echo === 构建 Docker 镜像 ===
docker compose -f %COMPOSE_FILE% build
goto end

:all
call :check_audio
if errorlevel 1 goto end
call :run_benchmark "paraformer onnx nano sensevoice"
goto end

:nano
call :check_audio
if errorlevel 1 goto end
call :run_benchmark "nano"
goto end

:sensevoice
call :check_audio
if errorlevel 1 goto end
call :run_benchmark "sensevoice"
goto end

:onnx
call :check_audio
if errorlevel 1 goto end
call :run_benchmark "paraformer onnx"
goto end

:paraformer
call :check_audio
if errorlevel 1 goto end
call :run_benchmark "paraformer"
goto end

:default
call :check_audio
if errorlevel 1 goto end
call :run_benchmark "paraformer onnx"
goto end

:check_audio
set count=0
for %%f in (%BENCHMARK_DIR%\*.wav %BENCHMARK_DIR%\*.mp3 %BENCHMARK_DIR%\*.flac %BENCHMARK_DIR%\*.m4a) do set /a count+=1
if %count%==0 (
    echo === 没有找到测试音频文件 ===
    echo 请将音频文件放入 %BENCHMARK_DIR%\ 目录
    echo 或运行: %0 prepare  ^(使用 TTS 生成测试音频^)
    echo.
    echo 支持格式: .wav .mp3 .flac .m4a
    exit /b 1
)
echo 找到 %count% 个音频文件
exit /b 0

:run_benchmark
echo === 运行 ASR Benchmark ===
echo 测试模型: %~1
echo 设备: CPU
echo.
docker compose -f %COMPOSE_FILE% build
docker compose -f %COMPOSE_FILE% run --rm benchmark python scripts/benchmark_asr.py --audio data/benchmark/ --device cpu --models %~1 --output data/benchmark/results.json
echo.
echo === 测试完成 ===
if exist "%BENCHMARK_DIR%\results.json" echo 结果已保存到: %BENCHMARK_DIR%\results.json
exit /b 0

:usage
echo 用法: %0 {prepare^|all^|nano^|sensevoice^|onnx^|paraformer^|build}
echo.
echo   prepare     - 准备测试音频数据
echo   all         - 测试所有 4 个模型
echo   onnx        - 对比 paraformer + ONNX (默认)
echo   nano        - 仅测试 Fun-ASR-Nano-2512
echo   sensevoice  - 仅测试 SenseVoice
echo   paraformer  - 仅测试当前 paraformer-zh
echo   build       - 仅构建镜像
goto end

:end
endlocal
