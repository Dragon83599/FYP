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

注意：PyPI 的 `torch` Linux wheel 自带 CUDA 运行库，`,venv` 会到 5~6 GB。
如果本机驱动版本低于该 wheel 的 CUDA 版本（`torch.cuda.is_available()` 为
False 并提示驱动过旧），torch 会自动回落到 CPU。ADR-007 的延迟基线本来就是
CPU 结果，因此不影响复现；若磁盘紧张，可改装 CPU-only wheel，但要在报告中
注明，因为它不改变依赖版本号却改变了构建产物。

2026-10-18 实测环境：Python 3.12.3、numpy 2.5.3、torch 2.14.1+cu130（驱动
576.88，CUDA 不可用，按 CPU 运行）、onnx 1.23.2、onnxruntime 1.30.0（仅
CPUExecutionProvider）、onnxscript 0.7.2、python-pptx 1.0.2、Pillow 13.0.0。

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

`synthetic_pcg.py` 同时写出 `segments/<record_id>.tsv`（CirCor 同格式的
`start end label`，1=S1、2=收缩期、3=S2、4=舒张期），并在 `recordings.csv`
中给出 `tsv_path` 与 `segment_count`。S1/S2 的时序模型使用
`systole = 0.30 * sqrt(RR / (60/70))`，夹在 0.20~0.34 s，保证高心率下 S2
不会塌到 S1 上。该模型在 2026-10-18 从固定 `0.30 * RR` 改过来，此前用合成
数据得到的 S1/S2 结论不可与新结果直接比较。

## S1/S2 事件级检测与评估

ADR-008 要求的事件级 precision/recall/F1、S1/S2 互换率和边界误差：

```powershell
python tools\ai\evaluate_s1s2.py --dataset data\processed\circor --out data\reports\s1s2-circor.json
python tools\ai\sweep_s1s2.py --dataset data\processed\synthetic-pcg
```

评估按患者汇总（患者内先累加 TP/FP/FN，再对患者求均值±标准差），同时给出
pooled 结果。`labeling_reliable` 以估算心率判定：超过 120 bpm 时收缩期与
舒张期接近，单听诊位置无法只靠时间间隔判定 S1/S2 顺序，这部分必须单独报告，
不能与低心率段平均掉。

检测器是确定性 DSP（20~85 Hz 带通、38 ms RMS 包络、4 s 分块中位数自适应阈值、
峰显著度抑制杂音、间期+过零率判定 S1/S2），参数在合成数据上扫描选出，尚未在
CirCor 上验证。指标、限制和失败模式见 `docs/s1s2-detector-baseline.md`。

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

## MFCC+差分对照臂

roadmap 要求的第三组对照（与 1D CNN、40 带 log-mel 逻辑回归并列）：

```powershell
.\.venv\Scripts\python.exe tools\ai\train_mfcc.py --data-dir data\processed\circor --out-dir data\reports\circor-cv-mfcc
```

- 特征：13 阶 MFCC + Δ + ΔΔ，每组取 mean/std/p90，默认 **117 维**；
  `--no-delta-delta` 退化为 78 维。
- 复用 `hs_features` 的 mel 滤波器组和 log-mel，复现 20~1000 Hz、4 kHz、40 带；
  DCT-II 与差分回归为自实现，不引入 scipy/librosa。
- 折划分、阈值选择和患者级聚合直接 import `cross_validate.py` 的同一套函数，
  保证与 CNN / log-mel 臂的数字可比，而不是口头声明可比。
- 合成数据上患者级 AUROC 也到 1.000，**log-mel 臂同样如此**——合成集只能验证
  管线，不能给三臂排序。

## 冻结患者级划分

不同日期的实验若各自按种子重算折，所谓“同一协议”就只是自我声明。冻结一次，
之后所有实验引用同一份文件和指纹：

```powershell
.\.venv\Scripts\python.exe tools\ai\freeze_splits.py --data-dir data\processed\circor
.\.venv\Scripts\python.exe tools\ai\train_mfcc.py --data-dir data\processed\circor --out-dir data\reports\circor-cv-mfcc --splits-file data\processed\circor\splits.csv
```

- 默认种子固定为 **20261008**，即 ADR-007（2026-10-08）患者级 5 折所用种子，
  因此不带参数冻结出来的折与既有 AUROC 数字（logreg 0.775/0.789、CNN
  0.811/0.834）**逐项相同**。这是刻意的：冻结的目的就是让新实验与旧数字可比。
  若要引入新的均衡划分，必须显式传 `--seed` 并另立 ADR，不能悄悄改默认值。
- 生成 `splits.csv`（含 provenance 前缀，忽略 `#` 行即可解析）和
  `splits-summary.json`（每折患者数、每折每类计数、指纹、种子、
  `seed_protocol`）。
- 报告里引用 `report.json["folds_source"]["fingerprint"]`；**指纹相同才叫同一协议**。
- 目前只有 `cross_validate.py` 和 `train_mfcc.py` 读取 `--splits-file`；
  `train_cnn.py` 与 `run_full_cv.ps1` 尚未接入，属于待办。

## 测试

```powershell
python -m unittest discover -s tests/python -v
npm test
```
