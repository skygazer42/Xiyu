# ASR 模型性能 Benchmark

本目录包含 ASR 模型性能对比测试脚本。

## 测试模型

| 模型 | 说明 | 安装依赖 |
|------|------|----------|
| `paraformer-zh` | 当前使用的模型 | `funasr` (已安装) |
| `paraformer-zh-onnx` | ONNX 优化版本 | `pip install funasr-onnx` |
| `Fun-ASR-Nano-2512` | 通义新模型 (0.8B) | `funasr` + 自动下载 |
| `SenseVoice-Small` | 极速模型 | `funasr` + 自动下载 |
| `backend-qwen3` | 通过 Xiyu 后端抽象层调用远程 Qwen3-ASR（vLLM） | 需先启动 Qwen3-ASR 服务 |
| `backend-vibevoice` | 通过 Xiyu 后端抽象层调用远程 VibeVoice-ASR（vLLM） | 需先启动 VibeVoice-ASR 服务 |
| `backend-router` | 自动路由（短音频 Qwen3 / 长音频或说话人 VibeVoice） | 需先启动上述服务 |

## 使用方法

### 1. 准备测试音频

将测试音频文件放入 `data/benchmark/` 目录：

```bash
# 单个文件
cp your_audio.wav data/benchmark/

# 或多个文件
cp audio1.wav audio2.wav audio3.wav data/benchmark/
```

### 2. 可选：准备参考文本

创建 `data/benchmark/ref.txt` 文件（TSV 格式）：

```
audio1.wav	这是第一个音频的正确转写文本
audio2.wav	这是第二个音频的正确转写文本
```

或 JSON 格式：

```json
{
  "audio1.wav": "这是第一个音频的正确转写文本",
  "audio2.wav": "这是第二个音频的正确转写文本"
}
```

### 3. 运行测试

```bash
# 测试所有模型
python scripts/benchmark_asr.py --audio data/benchmark/ --models all

# 仅测试当前模型和 ONNX 版本
python scripts/benchmark_asr.py --audio data/benchmark/ --models paraformer onnx

# 测试单个文件
python scripts/benchmark_asr.py --audio data/benchmark/test.wav

# 测试远程后端（需先按 README 配置并启动远程服务）
python scripts/benchmark_asr.py --audio data/benchmark/test.wav --models backend-qwen3 backend-vibevoice backend-router

# 指定设备
python scripts/benchmark_asr.py --audio data/benchmark/ --device cpu

# 包含参考文本计算 CER
python scripts/benchmark_asr.py --audio data/benchmark/ --ref data/benchmark/ref.txt

# 保存结果到 JSON
python scripts/benchmark_asr.py --audio data/benchmark/ --output results.json
```

### 4. 安装可选依赖

```bash
# ONNX Runtime 版本
pip install funasr-onnx

# 内存监控
pip install psutil
```

## 输出示例

```
================================================================================
ASR 模型性能对比结果
================================================================================

模型名称                       平均RTF      总时长(s)    推理时间(s)    内存(MB)      CER
--------------------------------------------------------------------------------
paraformer-zh                   0.0876       120.00        10.51       512.3    2.15%
paraformer-zh-onnx              0.0312       120.00         3.74       256.1    2.15%
Fun-ASR-Nano-2512               0.0654       120.00         7.85       890.2    1.80%
SenseVoice-Small                0.0078       120.00         0.94       320.5    2.45%
--------------------------------------------------------------------------------
```

## 指标说明

| 指标 | 说明 |
|------|------|
| RTF | Real-Time Factor，处理时间/音频时长，越小越快 |
| CER | Character Error Rate，字符错误率，越小越准 |
| 内存 | 峰值内存占用 |

## 注意事项

1. 首次运行会自动下载模型，可能需要较长时间
2. Fun-ASR-Nano-2512 模型较大 (~1.6GB)，请确保有足够磁盘空间
3. CPU 推理较慢，建议准备 10-60 秒的测试音频
4. 使用 `--no-warmup` 可跳过预热，但首次推理时间会不准确
