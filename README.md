# paper2zh — 英文论文 PDF → 中文 Markdown

把英文论文 PDF 变成中文 Markdown，**公式还是公式、图片还在原位、表格不散架**。

给 Windows 上不懂命令行的人用：解压 → 双击「1-开始安装.cmd」→ 双击「3-装进AI工具.cmd」，
之后只跟 AI 说话（Claude Code / Codex / Cursor / Gemini CLI / DSH 都行）。

> **适用平台：Windows 10 / 11（x64）。** 本项目由 `.cmd` 启动器 + 嵌入式 Windows Python +
> MinerU 的 Windows 基础包（DirectML / Vulkan）组成，**不支持 macOS 和 Linux**。
> 其他平台可以直接用 [MinerU](https://github.com/opendatalab/MinerU) 本体解析，
> 但本项目的「双击安装、装进 AI 工具」这一层不适用。

## 项目背景

这个项目起源于一个具体问题：让 AI 直接读英文论文 PDF，它往往会现场写一堆
`pdfplumber` / `PyPDF2` / OCR 脚本，反复试错、烧掉大量 token，最后公式、图片、表格还容易坏。

作者想做的不是「另一个 PDF 翻译器」，而是把这件事拆成两半：

- **解析、回填、渲染、质检**交给确定性脚本（MinerU + 本项目）；
- **翻译**交给 AI，因为这是它真正擅长的事。

于是有了这个 skill：AI 拿到的不是「一个 PDF」，而是一份已经切好块、公式已经保护好的段落清单；
它只负责翻译和判断，最终 Markdown 由 MinerU 官方渲染器生成。这样公式、图片、表格能逐字节保真，
译文质量则取决于执行翻译的模型。

目标用户很明确：**Windows 上不懂命令行的同事**——解压、双击两个 `.cmd`，之后只跟 AI 说话。
所以整个项目刻意没有 GUI、没有云服务、不装 torch、不要求管理员权限，也不要求用户会命令行。

## 下载

到 [Releases](https://github.com/Hyp3rY/paper2zh/releases/latest) 下载：

| 文件 | 大小 | 用于 |
|---|---|---|
| `pdf2zh-v1.0.0-win-x64-lite.zip` | 12.6 MB | **推荐**。自带嵌入式 Python 和全部源码，首次使用联网下载依赖与模型 |
| 完整离线包 | 约 1.94 GB | 断网环境。体积大，放在 Release 说明的网盘链接里 |

> 下载轻包后，直接看下一节「怎么装」。

## 建议配置与占用空间

| 项目 | 建议 |
|---|---|
| 系统 | Windows 10 / 11，64 位（**只支持 Windows**） |
| 内存 | 最低 8 GB，推荐 16 GB |
| 硬盘剩余 | 至少 6 GB（安装脚本会检查），推荐留 10 GB |
| 显卡 | 可选。有独显（≥ 6 GB 显存，支持 Vulkan / DirectML）会自动走 `standard` 档，快很多；没有就自动用 CPU 的 `basic` 档，慢 3–5 倍 |
| 网络 | 轻包首次安装要下载约 2.8 GB；完整包可完全断网 |
| 权限 | **不需要管理员权限**，不写注册表、不改 PATH |

**占用空间**

| 阶段 | 占用 |
|---|---|
| 轻包 zip | 12.6 MB |
| 轻包解压后 | 约 24 MB |
| 轻包装完（依赖 + 模型） | **约 2.8 GB**（实测 2.74 GB：依赖 760 MB + 模型 2,022 MB + Python 21 MB） |
| 完整包 zip | 1.94 GB |
| 完整包解压后 | 约 2.55 GB（依赖和模型都在里面，不再下载） |
| 每篇论文产物 | 约 5–10 MB（实测 9 MB 左右，图片多的论文更大） |
| 中文路径兼容副本 | 约 60 MB（`%LOCALAPPDATA%\pdf2zh\libs`，只有安装路径含中文时才生成） |

> 安装过程中 pip 还会占用 0.5–1 GB 缓存（`%LOCALAPPDATA%\pip\Cache`），装完可以删，不影响使用。

## 怎么装（三步）

1. 解压到一个找得到的位置（推荐纯英文路径，例如 `D:\Tools\pdf2zh`）
2. 双击 **`1-开始安装.cmd`**：下载依赖（约 0.8 GB）与模型（约 2 GB），断点续传
3. 双击 **`3-装进AI工具.cmd`**：复制 skill 到 `~\.agents\skills\pdf2zh` 与
   `~\.claude\skills\pdf2zh`，并登记安装位置

出问题双击 `2-检查一下.cmd`，它会用中文说清缺什么、该怎么办。

## 怎么用

对 AI 说：

> 把 D:\论文\xxx.pdf 翻成中文

或者更明确地调用这个 skill：`/pdf2zh 把 D:\论文\xxx.pdf 翻成中文`。

产物在 PDF 旁边的 `翻译结果\<文件名>\`：

```
zh.md            中文版
bilingual.md     中英对照版
images\          图片
qa.json          自动检查结果
先读我.txt        这份文件的说明
```

也可以直接拖 PDF 到 `翻译.cmd` 上（手动模式）。Git Bash / 英文终端里用纯 ASCII 的
`pdf2zh.cmd`，两者等价。

## 支持的 agent

| 工具 | 读取位置 |
|---|---|
| DSH / Codex / Cursor / Gemini CLI | `~\.agents\skills\pdf2zh` |
| Claude Code | `~\.claude\skills\pdf2zh` |
| WorkBuddy 国服 | `~\.workbuddy\skills\pdf2zh` |
| WorkBuddy 国际服 | `~\.workbuddy-ai\skills\pdf2zh` |
| 豆包 | `~\Doubao\skills\pdf2zh` 与 `…\.doubao\agent_mode\workspace\.user_skills\pdf2zh` |

安装脚本会先探测这些客户端在不在，装完再刷新；纯 UI 导入的客户端可以用同目录下生成的
`pdf2zh-skill.zip` 手动导入。WorkBuddy / 豆包装完需要**重启客户端**才会在「技能」里出现。

skill 只用通用字段（`name` / `description` / `whenToUse`），不用任何厂商私有扩展。

## 技术要点

- **本地解析**：MinerU 4.2（`mineru>=4.2,<5`），不调用任何云解析服务
- **不装 torch**：Windows 基础包自带 ONNX（DirectML）+ llama.cpp（Vulkan），
  有卡机器直接吃显卡，无卡机器退回 CPU
- **只翻文字**：改写 middle_json 里 `type=="text"` 的条目，再由 MinerU 官方渲染器出 md；
  公式、图片、表格一个字节都不碰
- **质检是脚本判的**：公式数量与逐条 LaTeX、图片路径集合、表格结构、交叉引用、
  英文残留率，五项全过才算出稿

## 还能导出 Word

对 AI 说「顺便导出 Word」，或手动：

```cmd
pdf2zh.cmd finish --workdir "<产物目录>" --docx     :: 出稿时一起导
pdf2zh.cmd export --workdir "<产物目录>"           :: 事后补导（不重翻、不重检）
```

会生成两份：`zh.docx`（中文版）和 `bilingual.docx`（中英对照）。公式是可编辑的 OMML、
图片内嵌，适合交稿或继续改。

## 已知限制

- **扫描件也能解析**（本地 OCR），但清晰度决定识别质量；建议翻完先核对前两页，数字版 PDF 仍然最稳
- 无独显的机器慢 3–5 倍（约 7 秒/页）
- 参考文献、代码块不翻译，保留原文
- 多栏/复杂版式的阅读顺序取决于 MinerU 的解析结果
- 图片按原文版面比例显示（`<img width=…>`），多子图按原版面合成表格；
  要纯 Markdown 语法可设 `PDF2ZH_PLAIN_IMAGES=1`，关掉子图排版设 `PDF2ZH_FIGURE_GRID=0`
- 安装路径含中文时，程序会自动把推理库复制到 `%LOCALAPPDATA%\pdf2zh\libs` 以兼容

## 许可

本项目代码采用 **MIT**。分发物里包含 MinerU 及其依赖，各自保留原许可证；
**MinerU 不是 MIT**，见 `THIRD_PARTY_NOTICES.md`。

## 免责与致谢

**非官方项目**，与 OpenDataLab 无关。感谢 [MinerU](https://github.com/opendatalab/MinerU)
提供解析能力——没有它这个项目不会存在。

