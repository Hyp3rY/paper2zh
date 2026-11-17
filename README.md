# pdf2zh — 英文论文 PDF → 中文 Markdown

把英文论文 PDF 变成中文 Markdown，**公式还是公式、图片还在原位、表格不散架**。

给 Windows 上不懂命令行的人用：解压 → 双击「1-开始安装.cmd」→ 双击「3-装进AI工具.cmd」，
之后只跟 AI 说话（Claude Code / Codex / Cursor / Gemini CLI / DSH 都行）。

## 为什么要做这个

让 AI 直接读 PDF，它往往会现场写一堆 `pdfplumber` / `PyPDF2` / OCR 脚本，反复试错、
烧掉大量 token，结果还差。这个项目把「解析」和「渲染」固化成确定性脚本，
AI 只负责翻译、判错和汇报——**它拿到的 skill 明确禁止自己造轮子**。

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

