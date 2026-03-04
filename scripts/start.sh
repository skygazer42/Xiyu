#!/bin/bash
set -e

MODE=${1:-gpu}
PORT=${PORT:-8000}

echo "======================================"
echo "Xiyu Speech Service Launcher"
echo "======================================"

case $MODE in
    gpu)
        echo "Starting with GPU support..."
        docker compose up -d
        ;;
    cpu)
        echo "Starting with CPU only..."
        docker compose -f docker-compose.cpu.yml up -d
        ;;
    build)
        echo "Building Docker image..."
        docker compose build
        ;;
    stop)
        echo "Stopping service..."
        docker compose down
        docker compose -f docker-compose.cpu.yml down 2>/dev/null || true
        docker compose -f docker-compose.models.yml down 2>/dev/null || true
        ;;
    logs)
        docker compose logs -f
        ;;
    models)
        PROFILE=${2:-}
        if [ -z "$PROFILE" ]; then
            echo "Usage: $0 models <pytorch|onnx|sensevoice|gguf|whisper|diarizer|qwen3|vibevoice|router|all|all-lite>"
            echo "Tip: vibevoice/router need VIBEVOICE_REPO_PATH=/path/to/VibeVoice"
            echo "Tip: all-lite = all without GGUF (no extra local model artifacts required)"
            exit 1
        fi
        echo "Starting model profile: ${PROFILE}"

        if [ "${PROFILE}" = "all-lite" ]; then
            docker compose -f docker-compose.models.yml \
              --profile diarizer \
              --profile pytorch \
              --profile onnx \
              --profile sensevoice \
              --profile whisper \
              --profile qwen3 \
              up -d
        else
            docker compose -f docker-compose.models.yml --profile "${PROFILE}" up -d
        fi

        # Best-effort: print the expected endpoint for the selected profile.
        case "$PROFILE" in
            pytorch) MODEL_PORT=${PORT_PYTORCH:-8101} ;;
            onnx) MODEL_PORT=${PORT_ONNX:-8102} ;;
            sensevoice) MODEL_PORT=${PORT_SENSEVOICE:-8103} ;;
            gguf) MODEL_PORT=${PORT_GGUF:-8104} ;;
            whisper) MODEL_PORT=${PORT_WHISPER:-8105} ;;
            diarizer) MODEL_PORT=${PORT_DIARIZER:-8300} ;;
            qwen3) MODEL_PORT=${PORT_XIYU_QWEN3:-8201} ;;
            vibevoice) MODEL_PORT=${PORT_XIYU_VIBEVOICE:-8202} ;;
            router) MODEL_PORT=${PORT_XIYU_ROUTER:-8200} ;;
            *) MODEL_PORT="" ;;
        esac
        if [ -n "${MODEL_PORT}" ]; then
            echo ""
            echo "Service URL: http://localhost:${MODEL_PORT}"
            echo "API Docs: http://localhost:${MODEL_PORT}/docs"
        elif [ "${PROFILE}" = "all" ] || [ "${PROFILE}" = "all-lite" ]; then
            echo ""
            echo "Started multiple services (profile=${PROFILE}). Common ports:"
            echo "  - PyTorch:   http://localhost:${PORT_PYTORCH:-8101}"
            echo "  - ONNX:      http://localhost:${PORT_ONNX:-8102}"
            echo "  - SenseVoice:http://localhost:${PORT_SENSEVOICE:-8103}"
            if [ "${PROFILE}" = "all" ]; then
                echo "  - GGUF:      http://localhost:${PORT_GGUF:-8104}"
            fi
            echo "  - Whisper:   http://localhost:${PORT_WHISPER:-8105}"
            echo "  - Qwen3:     http://localhost:${PORT_XIYU_QWEN3:-8201}"
            echo "  - Diarizer:  http://localhost:${PORT_DIARIZER:-8300}"
        fi
        ;;
    *)
        echo "Usage: $0 {gpu|cpu|models|build|stop|logs}"
        exit 1
        ;;
esac

if [ "$MODE" = "gpu" ] || [ "$MODE" = "cpu" ]; then
    echo ""
    echo "Service URL: http://localhost:${PORT}"
    echo "API Docs: http://localhost:${PORT}/docs"
    echo "WebSocket: ws://localhost:${PORT}/ws/realtime"
fi
