# 实测结论（findings）

> 规则：只写实测到的东西，附命令、版本、数字。没测的一律标「未验证」，不猜。
> 环境：Windows 11 Pro 10.0.26300 x64，Intel i9-9900KF，32 GB RAM，NVIDIA RTX 2070 8 GB / 驱动 580.97。
> 测试日期：2026-11-17。

---

## 1. 环境与网络

| 项 | 实测值 | 命令 / 依据 |
|---|---|---|
| 系统 Python | 3.14.7（`C:\Python314\python.exe`） | `python --version` |
| 自带运行时 | CPython **3.12.10** embeddable amd64 | `python.exe -c "import sys"` |
| 运行时体积 | zip 10.6 MB → 解压 **21.5 MB** | 目录求和 |
| 磁盘 | C 179 GB / D 302 GB / E 559 GB 可用 | `Get-PSDrive` |

镜像连通性（HTTP HEAD / GET，单位 ms）：

| 源 | 结果 |
|---|---|
| `mirrors.aliyun.com/pypi/simple` | 200，282 ms |
| `pypi.tuna.tsinghua.edu.cn/simple` | 200，681 ms |
| `modelscope.cn` | 200，233 ms |
| `pypi.org` | **超时**（本机直连不可用） |
| `www.python.org` | 时快时慢，多次 HEAD 超时 |
| `export.arxiv.org` / `arxiv.org` | 200，论文 2.11 MB / 2.6 s |

Python embeddable 国内镜像（均 200，10.6 MB）：阿里 / 华为云 / npmmirror / 清华。

> 结论：python.org 不能作为唯一来源；bootstrap 下载运行时必须走国内镜像。

### 1.1 硬件

`mineru-kit hardware detect --json` 实测输出（15:24:18）：

```json
{
  "ceiling": "advanced",
  "default_request_tier": "standard",
  "reason": "discrete_gpu",
  "cpu_count": 16,
  "small_backend": "onnx",
  "vlm_engine": "llama-cpp",
  "device": "dml:gpu",
  "pdf_image_tiers": ["flash", "basic", "standard", "advanced"],
  "warnings": []
}
```

日志里同时给出显卡细节：`NVIDIA GeForce RTX 2070`，`dedicated_memory_bytes=8394899456`（7.82 GiB），
`backend='dml'`，`reason='discrete_gpu'`。

---

## 2. MinerU 4.2.2 事实（直接读 wheel，不是看文档）

验证方式：从镜像下载 `mineru-4.2.2-py3-none-any.whl`（3.9 MB）解包。

### 2.1 入口

`entry_points.txt` 实测有 7 个 console_scripts：

```
mineru                = mineru.cli.main:main
mineru-api            = mineru.kit.commands.api_server:main
mineru-kit            = mineru.kit.main:main
mineru-models-download= mineru.kit.commands.models:download_main
mineru-openai-server  = mineru.kit.commands.vlm_server:main
mineru-router         = mineru.kit.router.cli:main
mineru-webui          = mineru.kit.commands.webui:main
```

提示词里用的 `mineru-kit` 是真的。因为禁止依赖 `.exe` shim，本项目统一用
`runtime\python\python.exe -m mineru.kit.main <子命令>`（`main.py` 里有 `__main__` 保护），已实测可跑。

### 2.2 CLI 参数（读 `mineru/kit/commands/*.py`）

```
mineru-kit parse <输入...> -o <输出> -f {markdown|middle_json|zip}
                --tier {flash|basic|standard|advanced} --ocr-mode {auto|txt|ocr}
mineru-kit models download [REPO] --tier {basic|standard} --source {auto|huggingface|modelscope}
mineru-kit models verify   --tier {basic|standard}
mineru-kit models show
mineru-kit hardware detect --json
mineru-kit hardware info   --json
```

**`-o/--output` 是必填**，提示词第 60 行的示例命令没有它。

### 2.3 SDK（重要修正）

```python
mineru.parser.parse(path, *, tier="standard", ocr_mode="auto",
                    image_analysis=True, page_range="", vlm_config=None) -> ParseResult
mineru.render.render(middle_json: MiddleJson, RenderFormat.MARKDOWN) -> str
```

`render()` 的实参**必须是 pydantic 模型 `docvortex.schema.MiddleJson` 实例**，
传普通 dict 会直接 `TypeError: render expects a MiddleJson instance`。
模型配置为 `extra="forbid", strict=True, validate_assignment=True`，所以正确用法是：

```python
mj = MiddleJson.model_validate(json.load(open("middle_json.json", encoding="utf-8")))
mj.pages[0].blocks[1].content[0].content = "译文"     # 赋值即校验
md = render(mj, RenderFormat.MARKDOWN)
```

`RenderFormat` 成员：`MARKDOWN / HTML / LATEX / DOCX / EPUB / STRUCTURED_CONTENT / CONTENT_LIST / CONTENT_LIST_V2 / PDF`。

### 2.4 依赖

- `Requires-Python: <3.15,>=3.11` → 3.12 可以。
- 硬依赖里 **没有 torch**，但有 `mineru-llama-cpp>=0.2.1`、`mineru-vl-utils`、`docvortex`、`gradio>=6.21`。
- `huggingface-hub[hf_xet]` 与 `modelscope` 都是包依赖（不是下载源），权重来源由 `MINERU_MODEL_SOURCE` 控制。

### 2.5 环境变量（源码中真实存在）

`MINERU_HOME`、`MINERU_CONFIG`、`MINERU_MODEL_SOURCE`、`MINERU_MODEL_SMALL_BACKEND`、
`MINERU_MODEL_VLM_ENGINE`、`MINERU_MODEL_STACK`、`MINERU_MODEL_ONNX_DEVICE`、
`MINERU_DEVICE_MODE`、`MINERU_LLAMA_CPP_BACKEND`、`MINERU_TELEMETRY_DISABLED`、
`MINERU_VIRTUAL_VRAM_SIZE`、`MINERU_ONNX_PROFILE_DIR`、`MINERU_CPU_NUM_THREADS`、`MINERU_LANG` 等。

`MINERU_LLAMA_CPP_BACKEND` 只接受 **`auto | cpu | vulkan | metal`**——**没有 cuda 选项**。

---

## 3. 三个假设

### A. 行内公式在 middle_json 里是什么形态？→ **独立条目，4.2 可以砍掉**

实测两篇论文 × 两个档位（`middle_json.json` 里所有 `content` 条目）：

| 样例 | 页数 | `text` 条目 | `equation_inline` 条目 | 含 `$` 的条目 | `$` 与正文混排 |
|---|---|---|---|---|---|
| Attention（standard） | 15 | 251 | 53 | **0** | **0** |
| Attention（basic） | 15 | 252 | 56 | **0** | **0** |
| Mask R-CNN（standard，双栏） | 12 | 235 | 22 | **0** | **0** |

行内公式是独立的 `{"type": "equation_inline", "content": "h_{t}"}`，**任何档位、任何排版都不混在正文里**：
basic 档只是把 LaTeX 拆成 `h _ { t } ,`（多了空格、质量更差），但**仍然是独立条目**。

→ 4.2 的占位符保护机制**不需要实现**。只翻 `type == "text"` 的条目，公式天然被跳过。

### B. `render()` 能否接受被修改过的 middle_json？→ **能，两篇论文都逐字节一致**

实测（`.cache/probes/probe_b_roundtrip.py`）：

| 样例 | 原始 md | 重新渲染 | 逐字节相同 | 改 251/234 个 text 条目后 |
|---|---|---|---|---|
| Attention | 44 177 字符 | 44 177 | ✅ | 44 407 字符，图片 5→5，`$$` 10→10，表格 2→2 |
| Mask R-CNN | 57 448 字符 | 57 448 | ✅ | 57 662 字符，图片 13→13，`$$` 0→0，表格 1→1 |

四步流程全部跑通：`json → MiddleJson.model_validate() → 改 content（赋值即校验）→ render()`。
改动只影响被改的文本，图片引用、公式块数、表格数**完全不变**。

→ 4.1 的「解析 → 改 middle_json → 官方渲染器出 md」架构**成立**，而且可验证。

**额外发现（重要）**：不是所有 `text` 条目都会进 markdown。用唯一标记逐条注入后统计：

| 样例 | `text` 条目 | 进入 md | 没进 md 的块类型 |
|---|---|---|---|
| Attention | 251 | 235 | `page_number` 14、`aside_text` 1、`footer` 1 |
| Mask R-CNN | 234 | 221 | `page_number` 12、`aside_text` 1 |

即 `page_number` / `aside_text` / `footer` 三类块**根本不渲染**。
第 3 批分块时要排除它们，否则白翻、白占预算、白触发 QA 误报。
（`ref_text` 会渲染，所以参考文献必须在翻译阶段显式排除，不能靠「不渲染」躲过去。）

### C. Windows 版 llama.cpp 用什么后端？→ **CPU + Vulkan，没有 CUDA；不需要 GPU 补丁包**

**直接证据**（比日志更硬）：包里自带 `llama-server.exe`，直接问它有哪些设备：

```text
$ llama-server.exe --list-devices
Available devices:
  Vulkan0: NVIDIA GeForce RTX 2070 (8220 MiB, 7442 MiB free)
```

```text
$ llama-server.exe --version
version: 0.6.0-dev (build 1, commit 86a2835)
built with MSVC 19.44.35229.0 for Windows AMD64
```

只有 Vulkan 设备，**没有任何 CUDA 设备**。

静态证据（解包 `mineru_llama_cpp-0.2.1-cp311-abi3-win_amd64.whl`，18.6 MB），`mineru_llama_cpp/bin/`：

| DLL | 大小 |
|---|---|
| `ggml-vulkan.dll` | 41.7 MB |
| `ggml-cpu.dll` | 1.1 MB |
| `ggml-base.dll` / `ggml.dll` | 0.6 / 0.1 MB |
| `llama.dll` / `mtmd.dll` / `llama-common.dll` / `llama-server-impl.dll` | 2.7 / 2.3 / 8.0 / 3.3 MB |

**没有 `ggml-cuda.dll`，也没有 cublas 相关 DLL。** 源码侧同样印证：`MINERU_LLAMA_CPP_BACKEND`
只接受 `auto|cpu|vulkan|metal`。

运行时证据（同一份 3 页输入，只改后端）：

| 后端设置 | `llama.cpp device policy` | 峰值显存 | 相对基线 |
|---|---|---|---|
| 默认（auto） | gpu (onnx_discrete_gpu) | — | — |
| `MINERU_LLAMA_CPP_BACKEND=vulkan` | native (explicit_gpu) | 3 811 MiB | **+2 912 MiB** |
| `MINERU_LLAMA_CPP_BACKEND=cpu` | cpu (explicit_cpu) | 1 912 MiB | +1 013 MiB（只有 DirectML 的 ONNX 部分） |

**结论：有卡机器直接走 Vulkan，不需要 `--with-gpu` 补丁包，省约 3 GB。**
仍有一点没测到：首次运行是否有 Vulkan shader 编译耗时（本机首跑未见明显停顿），
以及 `GGML_VK_*` 相关缓存目录位置。

### D（顺路验的）：`equation` 块的图片兜底是真的

把某个 `equation` 块的 `content`（LaTeX 字符串）清空后重新渲染：
`$$` 从 10 个降到 8 个，该块变成 `![](images/page_3_equation_6.jpg)`。

→ 提示词 4.1 说的「有些公式识别不出 LaTeX，会以图片形式输出」**属实**，机制是
equation 块的 `content` 为空、渲染器回退到 `image_path`。
**所以 equation 块一个字节都不能碰**（翻译、清洗、重排都不行），
否则会把「图片兜底」变成「空白公式」。
另外：把 LaTeX 换成中文垃圾串，渲染器照样包进 `$$` 输出（`$$` 数仍是 10），
说明**光数 `$$` 抓不到公式内容被污染**——QA 必须再加「公式块 LaTeX 与原文完全一致」的比对。

---

## 4. 安装实测

### 4.1 嵌入式 Python 的 `site-packages`（已验证）

embedded 发行版**不扫 site-packages**，`sys.path` 只认 `python312._pth`。修法（`build/_pth_patch.py` 已实现）：

```
python312.zip
.
../site-packages
import site
```

实测 `sys.path` 三项齐全，脚本幂等（重跑不重复加行），并对结果做 `Path.resolve()` 比对校验。

### 4.2 pip 引导（已验证）

embedded 发行版**没有 pip，也没有 ensurepip**（`python -m pip` 直接报 No module named pip）。
因此 7.2 里「先 `python -m pip install …`」这一步在原方案里会直接失败。

可行做法（已实测通过）：把 `pip-25.3-py3-none-any.whl`（1.7 MB）随轻包分发，解压到
`runtime\site-packages`，之后 `python -m pip` 立即可用，全程离线、不生成 .exe shim。

### 4.2.1 构建隔离在 embedded Python 下必然失败（实测踩坑）

第一次装 mineru 报错：

```
BackendUnavailable: Cannot import 'setuptools.build_meta'
```

原因不是没装 setuptools，而是 **pip 的构建隔离本质上是往临时目录装构建依赖再改 `PYTHONPATH`**，
而 embedded Python 因为有 `._pth`，**完全忽略 `PYTHONPATH`**，隔离环境里的 setuptools 永远 import 不到。

解法（已验证）：`pip install --no-build-isolation` + 先把 `setuptools`、`wheel` 装进
`runtime\site-packages`。`jieba` 是唯一需要现场构建的 sdist（19.2 MB tar.gz，无 wheel），构建约 10 秒。

`1-开始安装.cmd` 必须据此固定参数，否则干净机器上必然卡在这一步。

### 4.2.2 安装结果（实测）

| 项 | 数值 |
|---|---|
| `runtime\site-packages` | **760 MB / 34 382 个文件**（提示词估 1.2 GB，实测更小） |
| `runtime\python` | 21.5 MB |
| `models`（standard） | **2 001 MB**（ONNX 818 MB + VLM GGUF 1 183 MB） |
| 运行时合计 | 约 **2.8 GB** |
| `import mineru` | 605 ms；`from mineru.parser import parse …` 607 ms |
| `python -m mineru.kit.main version` | 692 ms，输出 `MinerU 版本: 4.2.2` / `Python 版本: 3.12.10` |
| 顶层包数 | 119（`pip freeze`），已写入 `requirements.lock` |

`mineru.__version__` 不存在（`__getattr__` 抛 AttributeError），版本要用
`importlib.metadata.version("mineru")` 或 CLI 拿。

### 4.3 下载限速（实测，重要）

同一台机器、同一文件（`mineru_llama_cpp-0.2.1` 轮子 18.6 MB）并发测速：

| 源 | 耗时 | 速率 |
|---|---|---|
| 阿里 `mirrors.aliyun.com/pypi` | 160.6 s | **118 KB/s** |
| 清华 `pypi.tuna.tsinghua.edu.cn` | 6.4 s | **2 952 KB/s** |

差 25 倍。阿里镜像对本机限速，**提示词 7.2 的镜像顺序（阿里 → 清华 → 官方）必须改成
「先测速再选 / 清华优先」**。1.5 GB 依赖用阿里要 3.5 小时，用清华约 9 分钟。

模型下载（ModelScope）实测总耗时约 12 分钟 / 2.0 GB，平均约 2.8 MB/s，可达性良好。

---

## 5. 解析产物（实测）

### 5.1 zip 结构（与提示词 4.1 一致）

```
attn-standard/
└─ attention-is-all-you-need.zip     1.22 MB
   ├─ markdown.md                    44 KB
   ├─ middle_json.json               0.12 MB
   ├─ structured_content.json        0.08 MB
   └─ images/                        14 个 jpg
```

注意：`-f zip` 只产出一个 zip 文件（不是目录），要先解压。

### 5.2 middle_json 是 v2.0，不是 2.x 的 `pdf_info`

```json
{ "schema": "docvortex.middle", "schema_version": "2.0",
  "metadata": {...}, "extensions": {}, "is_full_document": true,
  "pages": [ { "page_idx": 0, "blocks": [ ... ] } ] }
```

块（block）类型实测分布：`text 78`、`ref_text 40`、`paragraph_title 24`、`page_number 14`、
`equation 5`、`page_footnote 5`、`table 4`、`image 3`、`chart 2`、`doc_title 1`、`aside_text 1`、`footer 1`。

块内的 `content` 是**条目数组**，条目类型实测：`text 251`、`equation_inline 53`、
`hyperlink 17`、`table_caption 4`、`table_body 4`、`image_body 3`、`image_caption 3`、
`chart_caption 3`、`chart_body 2`、`equation` 块内的 `text`。

### 5.3 表格有两种语法

`table_body.content` 是 `<table><tbody>…</tbody></table>` HTML 字符串（含 `rowspan`/`colspan`），
不是行列结构。但渲染到 markdown 后**两种形态都会出现**：

| 样例 | table 块 | HTML `<table>` | Markdown 管道表 |
|---|---|---|---|
| Attention | 4 | 2 | 2 |
| Mask R-CNN | 14 | 1 | 13 |

→ QA 的「每张表行列数一致」必须**同时解析 HTML 和管道表**，而且不能假设
一个 table 块对应一种语法。

### 5.4 图片有两套，别搞混（QA 关键）

- `images/` 目录里 14 个文件：5 个 `image_*`/`chart_*` + 4 个 `table_*` + **5 个 `equation_*`**。
- `markdown.md` 里实际被引用的只有 **5 个**（`![](...)`），`equation_*.jpg` **一个都没被引用**：
  本样例的 5 个行间公式都识别成了 LaTeX，图片只是旁路产物。

→ QA 判「图片路径集合一致」时，**必须拿 source.md 与 zh.md 比**，
绝不能拿 `images/` 目录比，否则永远报错。

### 5.5 markdown 产物质量

| 指标 | 数值 |
|---|---|
| `$$…$$` 块 | 10 个 `$$` = **5 个行间公式** |
| 行内 `$` | 124 个 = **62 个行内公式** |
| 表格 | 4 个 table 块全部渲染：2 个 HTML `<table>` + 2 个 Markdown 管道表 |
| 图片引用 | 5 个，路径均为 `images/page_N_*.jpg` |

---

## 6. 性能实测

### 6.1 四种组合（Attention，15 页；Mask，12 页双栏）

| 样例 | 档位 | 设备 | 总墙钟 | 推理耗时 | 速度 | 每页 |
|---|---|---|---|---|---|---|
| Attention | standard | GPU | **29.8 s** | 20.39 s | 0.736 page/s | **1.36 s/页** |
| Attention | standard | CPU | 106.9 s | 99.07 s | 0.151 page/s | 7.13 s/页 |
| Attention | basic | GPU | 36.2 s | 30.28 s | 0.495 page/s | 2.02 s/页 |
| Attention | basic | CPU | **32.3 s** | 26.74 s | 0.561 page/s | 1.78 s/页 |
| Mask R-CNN（双栏） | standard | GPU | 27.7 s | 19.86 s | 0.604 page/s | 2.31 s/页 |

> 关键组合：**有卡 standard（29.8 s）≈ 无卡 basic（32.3 s）**；
> 有卡 basic（36.2 s）比有卡 standard 还慢，无卡 standard（106.9 s）是灾难。
> 所以档位不能「一律 standard」，也不能「一律 basic」，要按硬件分。

### 6.2 时间都花在哪（拆 20.4 s 的 standard）

从日志时间戳与进度条读出的阶段耗时：

| 阶段 | GPU（15 页） | CPU（15 页） |
|---|---|---|
| ONNX 会话初始化 | 1.4 s | 1.4 s |
| llama.cpp 加载 VLM | 2.7 s | 2.7 s |
| Layout Predict | 3 s | 2 s |
| OCR-det | 4 s | 4 s |
| **VLM Predict**（2 批 10 + 56 项） | **~9 s** | **~59 s** |
| 行内公式识别 | 56 项全部 VLM 成功，MFR 回退 0 | 同左 |
| **合计（推理）** | 20.4 s | 99.1 s |

basic 档没有 VLM，但多出 **MFR Predict 9 s**（公式识别模型），所以 GPU 上反而更慢。

> 回答提示词第 5 节的问题 3：**VLM 在 GPU 上约占 45–50%，不是 80%**；
> 到了 CPU 上 VLM 占到 ~60%，这才是「无卡机必须降 basic」的原因。
> 折腾 DirectML 不算白费——layout/OCR/VLM 加载那部分是小模型在扛。

### 6.3 三个问题的答案

| 问题 | 结论 | 依据 |
|---|---|---|
| 1. 有卡机默认走哪个档 | **standard** | `hardware detect` 的 `default_request_tier=standard`；本机 standard 29.8 s 且质量更高 |
| 2. 要不要 `--with-gpu` 补丁包 | **不要** | llama.cpp 自带 Vulkan（`--list-devices` 可见），不碰 torch/CUDA，省约 3 GB |
| 3. 小模型 / VLM 各占多少 | GPU：VLM ~45%；CPU：VLM ~60% | 6.2 的阶段耗时 |

**追加结论（提示词没问但很关键）**：`hardware detect` 把本机上限标成 `advanced`，
但 `mineru-kit models` 只接受 `basic|standard` 两个档位资源，`advanced` 具体指什么
**未验证**；第 3 批不要拿它当「更强档」去调，先用 standard。

---

## 7. 与提示词的差异（已按此实现或需确认）

1. **`mineru-kit parse` 的 `-o` 必填** —— 包装脚本固定传，不改设计。
2. **embedded Python 无 pip** —— 改为「随包附 pip wheel + 解压安装」，见 4.2。
3. **构建隔离不可用** —— bootstrap 必须 `--no-build-isolation` 并预装 setuptools/wheel，见 4.2.1。
4. **`render()` 只吃 pydantic `MiddleJson`** —— 第 4 节的伪代码要写成 `model_validate` + 属性赋值。
5. **middle_json 是 v2.0 结构**（`pages[].blocks[].content[]`），不是 `pdf_info`；4.1/4.3 里「span」对应「content 条目」。
6. **4.2 占位符保护不需要实现** —— 假设 A 两篇论文两个档位都判定行内公式独立成条目。
7. **`gradio`/`docvortex` 是硬依赖** —— site-packages 实测 760 MB，7.1 表里 1.2 GB 可改小。
8. **Python embeddable 体积 21.5 MB**，提示词写 40 MB。
9. **镜像顺序要改** —— 本机阿里限速，清华快 25 倍，见 4.3。
10. **默认档不能写死一个** —— 有卡 standard（29.8 s）、无卡 basic（32.3 s）；
    无卡跑 standard 是 106.9 s。硬设 standard 会让无卡用户等 3 倍。
11. **提示词要求「干净机器」「有卡/无卡各一台」验收** —— 本开发机只有一台带 RTX 2070 的机器，
    无卡机只能用 `MINERU_DEVICE_MODE=cpu` 模拟，不能算真机验收。
12. **模型落盘位置**：`MINERU_HOME=<包>\home` 时，MinerU 4.2.2 把权重放在 `home\models\`，
    不是提示词 6.1/7.2 写的根目录 `models\`。第 4 批必须在「改布局」和「改文档+标记路径」之间定一个，
    不能两头都写一套。
13. **QA 公式闸门不够用**：只数 `$$` 抓不到公式内容被污染（换成中文串 `$$` 数不变），
    必须逐块比对 equation 块的 LaTeX 字符串完全一致，另外 equation 块可能只有图片兜底。



---

## 8. 第 3 批实现期实测（脚本层）

### 8.1 嵌入式 Python 的 `._pth` 还会关掉「脚本目录自动进 sys.path」

`python scripts\pdftozh.py` 直接 `ModuleNotFoundError: No module named 'paths'`。
原因：只要存在 `python312._pth`，CPython 就不再往 `sys.path[0]` 塞脚本目录。

→ 每个入口脚本顶部必须显式：

```python
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
```

这条不写，干净机器第一次双击就会报错。

### 8.2 翻译单元怎么设计（agent 翻译版）

实测结论：**按块翻、公式用 `$...$` 原位保留、再按 `$...$` 拆回条目** 是可行的：

| 步骤 | 做法 | 验证 |
|---|---|---|
| 拼原文 | 块内 `text` 条目串起来，`equation_inline` 转成 `$latex$` | 34 段样例里 16 个行内公式全部保留 |
| 回填 | `rebuild_spans()` 按 `$...$` 拆成 `TextSpan` / `EquationInlineSpan` | 渲染后公式 16/16 |
| 校验 | `apply` 比对公式列表（去空白、保序）+ 必须含中文 + 不许有 markdown | 破坏测试立即退回 |
| 双语 | 同一条目里写 `英文\n\n中文` | 渲染器保留空行（第 2 批已验），`bilingual.md` 正常 |

### 8.3 表格里藏着 `<eq>` 公式（差点翻坏）

`table_body.content` 的 HTML 里有 `<td><eq>d_{model}</eq></td>` 这种结构。
按「文本节点」遍历会把 `<eq>` 里的 LaTeX 当文字送去翻译，译文一回填公式就废了。

修法：
- `html_units()` 跳过 `<eq>…</eq>` 内部，以及含 `\ ^ _ { }` 的 LaTeX 特征文本；
- `qa.py` 的表格指纹增加 `<eq>` 片段比对。

实测：4 张表、252 个单元格，标签骨架与单元格数完全不变，`<eq>N</eq>`、`<eq>d_{model}</eq>` 原样保留。

### 8.4 「只数 `$$`」真的抓不住公式被改（验收证据）

故意把译文里 `$h_{t}$` 的一个 `$` 删掉，然后：

| 检查方式 | 结果 |
|---|---|
| 只比 `$$` / `$` 数量 | 数量法第一版 **通过**（错的） |
| 比 LaTeX 指纹（逐条公式内容） | **抓住**：第 1 条 LaTeX 变了、条目数 8→7、退出码 6 |

所以 `qa.py` 现在同时做「数量」和「逐条 LaTeX 指纹」两道，缺一不可。

### 8.5 其它实现期结论

- `render()` 必须吃 pydantic `MiddleJson` 实例；写回时用 `mj.to_dict(skip_defaults=False)`。
- 表格两种渲染形态（HTML / 管道表）都会出现，QA 的表格指纹基于 middle_json（HTML），比对的是「结构没被改」，与 md 形态无关。
- `page_number` / `aside_text` / `footer` 不渲染，已从翻译单元里排除；`ref_text`（参考文献）渲染但按 4.3 不翻。
- 3 页样例端到端：`prepare` 34 段 → `apply` 34/34 → `finish` → 五道闸门全过。

---

## 9. 第 4 批实测（打包与安装）

### 9.1 轻包实测体积

| 组成 | 大小 |
|---|---|
| 嵌入式 Python 3.12.10 | 21.5 MB |
| skill 源码 + 启动器 + 文档 + pip wheel + 样例 | ~1.8 MB |
| 合计（目录） | **23.3 MB** |
| zip | **12.6 MB** |

提示词估的是 40–50 MB，实测更小。依赖（760 MB）与模型（2.0 GB）都不进包。

### 9.2 镜像选择必须按吞吐量，不能按 HEAD 延迟

| 源 | HEAD 延迟 | 实测下载 |
|---|---|---|
| 阿里 | 0.2 s | 118–163 KB/s |
| 清华 | 1.3 s | 54 KB/s（本次索引页） / 2.9 MB/s（大文件实测） |
| 中科大 | 0.2 s | 334 KB/s |

HEAD 快 ≠ 下载快。`bootstrap.py` 现在下载一小段真实索引页比吞吐量再选源。

### 9.3 中文安装路径会让 standard 档直接失效（重要）

在 `C:\Users\<用户名>\Desktop\测试 目录\pdf2zh-lite` 下：

| 档位 | 结果 |
|---|---|
| basic | ✅ 3 页正常（75 段 / 14 231 字） |
| standard | ❌ `No mapping for the Unicode character exists in the target multi-byte code page` |

同一份 PDF、同一台机器，把包换到纯英文路径后 standard 正常。
机理：standard 档要加载 `mineru_llama_cpp/bin` 下的 Vulkan/llama 原生库，
这些库对非 ASCII 路径处理不好；basic 档只用 ONNX，不受影响。

处置（已实现）：`prepare` 在 standard + 中文路径时**立即报错**并给出两条路
（挪到纯英文路径 / 改用 `--tier basic`），不做静默降级。

### 9.4 其它安装期结论

- `~\.claude\skills`、`~\.agents\skills` 装完后，DSH 的 skill 列表当场出现 `pdf2zh`，
  说明「装进 AI 工具」这条路是真通的。
- 嵌入式 Python 的 `._pth` 同时关掉了「脚本目录进 sys.path」，所有入口都要自己登记。
- 轻包里 `skill\pdf2zh\scripts` 与开发态 `scripts` 两种布局并存，`paths.find_root()`
  已按「环境变量 → 脚本相对位置 → 配置文件 → 常见路径」兜住。

### 9.5 中文路径问题：根因与修复（推翻 9.3 的「无解」结论）

**根因不是模型路径，也不是输出路径**，而是 `mineru_llama_cpp` 用窄字符（mbcs）API
加载自己那 60 MB 的 DLL：`ctypes.CDLL("…中文…\\ggml-vulkan.dll")` 在非 ASCII 路径下
抛 `No mapping for the Unicode character exists in the target multi-byte code page`。
basic 档只用 ONNX，不经这条路径，所以没事。

实验对照（同一份 PDF、同一个中文安装目录）：

| 条件 | 结果 |
|---|---|
| 原样，standard | ❌ mbcs 报错 |
| MINERU_HOME 换成英文（模型英文） | ❌ 仍报错 → 排除模型路径 |
| 输出目录换成英文 | ❌ 仍报错 → 排除输出路径 |
| 把 `mineru_llama_cpp` 复制到英文目录并让它先被 import | ✅ **成功** |

**修复（已实现并验证）**：

1. `paths.ensure_ascii_libs(root)` 在安装路径含非 ASCII 字符时，把
   `site-packages\mineru_llama_cpp`（60 MB）复制到
   `%LOCALAPPDATA%\pdf2zh\libs`，用源目录 mtime 做版本戳，避免每次重拷；
2. 把这个英文目录写进 `python312._pth`，位置在 `../site-packages` **之前**，
   于是 import 走英文副本，DLL 路径也变英文；
3. `pdftozh.py prepare`（standard 档）和 `doctor.py` 的路径检查会自动调用它；
4. 不改档位、不静默降级；英文路径下这段逻辑直接跳过。

实测：`C:\Users\<用户名>\Desktop\测试 目录\pdf2zh-lite` 下 fresh 运行
`prepare --tier standard` → 自动复制推理库、自动改 `._pth`、解析成功（3 页 / 78 段）。

代价：中文路径的机器会多占约 60 MB（`%LOCALAPPDATA%`），换机器后重跑一次自检即可重建。
