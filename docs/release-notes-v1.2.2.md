# paper2zh v1.2.2

这个版本主要改三件事：**输出文件改成中文名**、**升级不再重下依赖和模型**、**单栏阅读和 Word 里的图片排版更合理**。

## 输出文件改成中文名

`finish` 仍然先在内部生成 `zh.md` / `bilingual.md`，等质检和可选的 Word 导出都完成后，自动改名：

```
中文版.md
中英对照.md
中文版.docx
中英对照.docx
```

- `先读我.txt` 里的文件名同步更新；
- 旧目录里的 `zh.md`、`bilingual.md`、`zh.docx`、`bilingual.docx` 会在下一次 `finish` / `export` 时自动迁移，不会留下旧名；
- 旧产物仍可被质检读取，不影响已经翻好的内容；
- GitHub 上 `\tag{}` 会渲染成竖排表格，输出改为普通公式编号，GitHub 与本地阅读器都能正常显示。

## 图片排版

### Markdown：半栏图不再显得太小

双栏论文里的半栏图，在单栏 Markdown 中放大到接近正文宽：

- 占页宽小于 70% 的图，输出宽度为 **720 px**；
- 720 px 是宽屏上限，2K/4K 屏上也不会大得离谱；
- 通栏图保持原比例；
- 窄屏由 `max-width:100%` 兜底，不会溢出。

不想放大可设：

```text
PAPER2ZH_FIGURE_EXPAND=0
```

### Word：单图放大，多图并排

导出 Word 后会自动做一次版式收尾：

- 连续的图片段落，如果不是通栏大图，自动合成 2 列/3 列表格；
- 半栏单图放大到页宽；
- 通栏图继续保持原比例，不硬压成两列；
- 图注、正文、公式段落不动。

这样双栏论文里的多子图不会在 Word 里一张一行地竖排。

## 升级不用重下依赖和模型

之前的升级说明会解压到新目录再跑 `bootstrap all`，等于重新下载约 2.8 GB。现在改成：

1. 从 `%USERPROFILE%\.paper2zh\config.json` 找到旧安装目录；
2. 下载最新 lite 包，解压到临时目录；
3. 只复制 `skill\paper2zh\*` 和 `paper2zh.cmd` 到旧目录；
4. 用旧目录里的 Python 跑一次 `install_skill.py`；
5. `doctor.py --quick` 自检。

依赖和模型都留在旧目录，正常十几秒到一分钟。只有 `requirements.lock` 真的变了才需要跑 `bootstrap.py deps`，模型仍然不用重下。详细命令见 [AI-INSTALL.md](../AI-INSTALL.md) 的「升级旧版本」。

## 验证

- 新增 `tests/test_layout.py`：覆盖 Markdown 半栏图放大、通栏图保持、Word 多图表格；
- 新增 `tests/test_output_names.py`：覆盖 md/Word 改名、重复执行覆盖；
- `tests/test_stitch.py` 和原有回归全部通过；
- 示例论文的在线版、本地 9B 版仍为五项质检全过。

## 下载

| 文件 | 大小 | SHA256 |
|---|---|---|
| `paper2zh-v1.2.2-win-x64-lite.zip` | 12.6 MB | `2DCB1E68A2E90DC8BB200EF8C9964847B2B8B4D39518A25EFB53BC7CE2ED70BD` |
| `paper2zh-v1.2.2-win-x64-full.zip` | 1.94 GB | `B856001082871BDCFF1AC7E4140561DAB77302BA99D535B45A13E6D11D1D1761` |

## 说明

- 工具**没有自动升级**；升级只换脚本，不动已有安装里的依赖、模型和翻译结果；
- 从 1.2.1 升级时，按 `AI-INSTALL.md` 的「升级旧版本」操作即可；
- 如果你已经下载过旧的 1.2.2 包，请用上面的新哈希替换；版本号仍然是 1.2.2。
