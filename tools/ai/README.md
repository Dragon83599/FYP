# AI 心音基线工具

本目录实现从数据准备到主机资源预算的最小闭环。所有脚本默认把大数据写入
`data/`，该目录已在 `.gitignore` 中排除。数据集许可：CirCor DigiScope 1.0.3，
ODC-By 1.0。

## 环境

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r tools\ai\requirements.txt
```

纯 NumPy 的逻辑回归与 DSP 代码也可使用 Codex 自带 Python 运行；CNN 训练、
ONNX 导出和量化需要上面的 venv。

## 数据准备

完整公开包约 471 MB：

```powershell
node tools\ai\download_circor_parallel.mjs --concurrency 12 --chunk-bytes 1048576
python tools\ai\prepare_circor.py
```

`download_circor_parallel.mjs` 使用带 Range 的分块请求，支持断点续传；受限网络下
单流会停滞，实测并行分块聚合吞吐约 40 KB/s，完整包仍需数小时。单流脚本
`download_circor.mjs` 保留为简单回退。

受限网络下可先下载平衡子集（当前实测约 15.9 MB / 158 个文件）：

```powershell
python tools\ai\download_circor_subset.py --subjects-per-class 12 --include-annotations
python tools\ai\prepare_circor.py --extract-dir data\raw\circor\subset --out-dir data\processed\circor-subset
```

`prepare_circor.py` 只使用受试者级（患者级）划分，`Unknown` 标签不进入训练。

## 合成数据闭环

在真实数据下载完成前，可用合成 PCG 验证代码路径。合成结果不是临床证据：

```powershell
python tools\ai\synthetic_pcg.py --subjects 120
python tools\ai\train_baseline.py --data-dir data\processed\synthetic-pcg --out-dir data\reports\synthetic-logreg
```

## 基线模型

逻辑回归基线（NumPy，无框架依赖）：

```powershell
python tools\ai\train_baseline.py --data-dir data\processed\circor-subset --out-dir data\reports\circor-subset-logreg
```

小型 1D CNN（PyTorch CPU，输出参数、MACs、延迟、ONNX FP32/INT8 与资源预算）：

```powershell
.\.venv\Scripts\python.exe tools\ai\train_cnn.py --data-dir data\processed\circor-subset --out-dir data\reports\circor-subset-cnn
```

输出：

- `report.json` / `report.md`：训练历史、阈值、窗口级与患者级指标。
- `resource-budget.md`：按 `docs/ai-model-resource-budget-guide.md` 模板填写，
  目标板未测项明确标记为“待实测”。
- `model-fp32.onnx`、`model-int8.onnx`：PC 上的大小和延迟参考。

## 窗口筛选对照

比较“全部窗口”和“仅分割标注的高质量窗口”两种策略（逻辑回归 + CNN，共 4 组）：

```powershell
powershell -NoProfile -File tools\ai\run_window_selection_experiments.ps1
```

报告写入 `data/reports/circor-subset-{logreg,cnn}-{all,annotated}/`。当前数据只有
24 名受试者、4 名测试患者，结果是方向性候选证据，不能作为最终模型结论。

## 患者级 5 折交叉验证

完整公开训练集（874 名有标签受试者）的患者级分层分组交叉验证：

```powershell
powershell -NoProfile -File tools\ai\run_full_cv.ps1
```

该脚本依次运行逻辑回归/CNN × 全部窗口/标注窗口共 4 组，每组 5 折；每折从训练池
再分约 15% 患者作验证集选阈值。报告写入 `data/reports/circor-cv-*/`，包含每折指标、
跨折均值±标准差和 pooled 患者级 95% 置信区间。

## 测试

```powershell
python -m unittest discover -s tests/python -v
npm test
```
