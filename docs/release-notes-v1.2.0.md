# paper2zh v1.2.0

把英文论文 PDF 转成中文 Markdown：**公式还是公式、图片还在原位、表格不散架**。
本地 MinerU 解析，翻译可以由当前 AI 模型完成，也可以交给本机 LM Studio；不调用任何云解析服务。

> 本项目早期叫 `pdf2zh`，因与 PDFMathTranslate 的同名工具冲突而更名。
> 环境变量、配置目录、skill 名称都已切换为 `paper2zh` / `PAPER2ZH_*`。

## 本次更新

### 本地模型直连（重点）

- 新增 `local` 子命令：逐段调用 LM Studio 原生接口，`reasoning: off`，
  单段上下文约 500 token，8 GB 显存 / 8k 上下文也能跑；
- `prepare --local-model "qwen/qwen3.5-9b"` 会把模型写进产物目录的 `local.json`，
  之后 `local` 不用再传参数；
- `local --probe` 检查本机已加载的模型；**只使用已经加载的模型**，
  不会自动加载，也不会同时开多个；
- 解析完如果检测到本地模型，工具会强制先问用户；
  `next` 在未选择时会返回 `needs_choice`，用 `next --agent` 表示继续用当前 AI 模型。

### 质量后处理（零 token）

- `finish` 按 `glossary.csv` 统一术语（前行侧/后行侧、迎角、尾迹等），表格单元格也处理；
- 作者行被音译时，自动恢复为「作者：原文」；
- 中文之间的 ` - ` 统一成 `-`；
- `terms` 升级：过滤泛词、单复数归并、新增 2–3 词短语候选，输出带 `kind` 列；
- **术语必须由当前在线模型确认**，本地模型只按表翻译，不参与选词。

### 图注与版式

- 修复多子图图注错位：Figure 8/9、10/11 这类「图注挂在下一组首图」的情况，
  现在会先归位再合并，输出为「4 图表格 + 图注」；
- 不再把两组 4 张子图并成一张 8 图表。

### 成本与行为

- 新增「最小修复原则」：质检通过就停，不再做连字符、粘性/黏性、措辞之类的顺手润色；
- 汇报只报页数、耗时、闸门结果和修改 uid 数量，不贴原文/译文；
- 实测 18 页论文：纯在线约 ¥0.23（谷时），本地翻译 + 在线调度约 ¥0.06（谷时）。

### 修复

- `apply` 的 `NameError: config_path / base_url`；
- 控制台中文乱码（不再把真正的控制台改成 cp936）；
- 安装 skill 时目录被占用导致 `Permission denied`，现在会改名重试，单个客户端失败不中断其它。

## 下载

| 文件 | 大小 | 说明 |
|---|---|---|
| `paper2zh-v1.2.0-win-x64-lite.zip` | 12.6 MB | **推荐**。自带嵌入式 Python 与全部源码，首次使用联网下载依赖（约 760 MB）和模型（约 2 GB） |
| `paper2zh-v1.2.0-win-x64-full.zip` | 1.94 GB | 完整离线包，断网环境用；依赖与 standard 模型都已包含 |

## 安装（两步）

1. 解压到任意位置（推荐纯英文路径，例如 `D:\Tools\paper2zh`）
2. 双击 **`1-开始安装.cmd`**，等它跑完（约 5–10 分钟）

出问题再双击一次同一个文件，它会跳过已完成的部分、重新检查并修复。

> 不想自己点？把 [AI-INSTALL.md](https://github.com/Hyp3rY/paper2zh/blob/main/AI-INSTALL.md)
> 里的提示词整段发给一个能执行命令的 AI。

## 使用

```text
/paper2zh 把 D:\论文\xxx.pdf 翻成中文
```

需要本地模型时：

```cmd
paper2zh.cmd prepare "论文.pdf" --local-model "qwen/qwen3.5-9b"
paper2zh.cmd local --workdir "<产物目录>" --limit 20
paper2zh.cmd finish --workdir "<产物目录>"
```

## 系统要求

- Windows 10 / 11，64 位（**仅支持 Windows**）
- 内存 8 GB 起，推荐 16 GB
- 磁盘剩余 ≥ 6 GB；装完约 2.8 GB
- 本地模型：LM Studio + 一个已加载的模型；8 GB 显存建议 Qwen3.5-9B Q4，
  上下文 16384、GPU Offload 拉满、Enable Thinking 关、Temperature 0.2

## 校验

```text
SHA256(paper2zh-v1.2.0-win-x64-lite.zip)
725D530F871B1CDC59E4AC615089107C220B062187891DF204B4BFFC2BE86955

SHA256(paper2zh-v1.2.0-win-x64-full.zip)
AFC9D4C91ADE3805DE03A666B5B27F84FD473799166F864C37CCFE0CFB86205B
```

## 已知限制

- 扫描件能解析（本地 OCR），但清晰度决定识别质量，建议翻完先核对前两页；
- 参考文献、代码块不翻译，保留原文；
- 多栏 / 复杂版式的阅读顺序由 MinerU 决定；
- 本地 9B 译文可读、公式数字完整，但术语漂移和少量直译仍需人工抽查；
- 术语表要先用在线模型确认，本地模型不负责选词。

## 致谢

解析能力来自 [MinerU](https://github.com/opendatalab/MinerU)。非官方项目，与其无隶属关系。
