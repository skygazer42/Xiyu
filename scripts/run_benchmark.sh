#!/bin/bash
# ASR Benchmark 运行脚本
# 使用方法:
#   ./scripts/run_benchmark.sh                     # 默认测试 paraformer + onnx
#   ./scripts/run_benchmark.sh all                 # 测试所有模型
#   ./scripts/run_benchmark.sh prepare             # 准备测试数据
#   ./scripts/run_benchmark.sh nano                # 仅测试 Fun-ASR-Nano
#   ./scripts/run_benchmark.sh sensevoice          # 仅测试 SenseVoice

set -e

COMPOSE_FILE="docker/compose/legacy/docker-compose.benchmark.yml"
IMAGE_NAME="xiyu-benchmark:latest"
BENCHMARK_DIR="data/benchmark"

# 确保 benchmark 数据目录存在
mkdir -p "$BENCHMARK_DIR"

# 检查是否有测试音频
check_audio() {
    count=$(find "$BENCHMARK_DIR" -maxdepth 1 \( -name "*.wav" -o -name "*.mp3" -o -name "*.flac" -o -name "*.m4a" \) 2>/dev/null | wc -l)
    if [ "$count" -eq 0 ]; then
        echo "=== 没有找到测试音频文件 ==="
        echo "请将音频文件放入 $BENCHMARK_DIR/ 目录"
        echo "或运行: $0 prepare  (使用 TTS 生成测试音频)"
        echo ""
        echo "支持格式: .wav .mp3 .flac .m4a"
        return 1
    fi
    echo "找到 $count 个音频文件"
    return 0
}

# 构建镜像
build_image() {
    echo "=== 构建 Docker 镜像 ==="
    docker compose -f "$COMPOSE_FILE" build
}

# 准备测试数据
prepare_data() {
    echo "=== 准备测试数据 ==="
    build_image
    docker compose -f "$COMPOSE_FILE" run --rm benchmark \
        python scripts/prepare_benchmark_data.py
}

# 运行 benchmark
run_benchmark() {
    local models="${1:-paraformer onnx}"

    if ! check_audio; then
        exit 1
    fi

    echo "=== 运行 ASR Benchmark ==="
    echo "测试模型: $models"
    echo "设备: CPU"
    echo ""

    build_image

    docker compose -f "$COMPOSE_FILE" run --rm benchmark \
        python scripts/benchmark_asr.py \
            --audio data/benchmark/ \
            --device cpu \
            --models $models \
            --output data/benchmark/results.json

    echo ""
    echo "=== 测试完成 ==="
    if [ -f "$BENCHMARK_DIR/results.json" ]; then
        echo "结果已保存到: $BENCHMARK_DIR/results.json"
    fi
}

# 主逻辑
case "${1:-default}" in
    prepare)
        prepare_data
        ;;
    all)
        run_benchmark "paraformer onnx nano sensevoice"
        ;;
    nano)
        run_benchmark "nano"
        ;;
    sensevoice)
        run_benchmark "sensevoice"
        ;;
    onnx)
        run_benchmark "paraformer onnx"
        ;;
    paraformer)
        run_benchmark "paraformer"
        ;;
    build)
        build_image
        ;;
    default)
        run_benchmark "paraformer onnx"
        ;;
    *)
        echo "用法: $0 {prepare|all|nano|sensevoice|onnx|paraformer|build}"
        echo ""
        echo "  prepare     - 准备测试音频数据"
        echo "  all         - 测试所有 4 个模型"
        echo "  onnx        - 对比 paraformer + ONNX (默认)"
        echo "  nano        - 仅测试 Fun-ASR-Nano-2512"
        echo "  sensevoice  - 仅测试 SenseVoice"
        echo "  paraformer  - 仅测试当前 paraformer-zh"
        echo "  build       - 仅构建镜像"
        exit 1
        ;;
esac
