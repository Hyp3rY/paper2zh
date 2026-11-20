# paper2zh v1.2.2

这个版本只做两件事：**把输出文件改成中文名**，以及**把升级改成只换脚本**。

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
- 旧产物仍可被质检读取，不影响已经翻好的内容。

## 升级不用重下依赖和模型

之前的升级说明会解压到新目录再跑 `bootstrap all`，等于重新下载约 2.8 GB。现在改成：

1. 从 `%USERPROFILE%\.paper2zh\config.json` 找到旧安装目录；
2. 下载最新 lite 包，解压到临时目录；
3. 只复制 `skill\paper2zh\*` 和 `paper2zh.cmd` 到旧目录；
4. 用旧目录里的 Python 跑一次 `install_skill.py`；
5. `doctor.py --quick` 自检。

依赖和模型都留在旧目录，正常十几秒到一分钟。只有 `requirements.lock` 真的变了才需要跑 `bootstrap.py deps`，模型仍然不用重下。详细命令见 [AI-INSTALL.md](../AI-INSTALL.md) 的「升级旧版本」。

## 验证

- 新增 `tests/test_output_names.py`：覆盖 md/Word 改名、重复执行覆盖；
- `tests/test_stitch.py` 和原有回归全部通过；
- 示例论文的在线版、本地 9B 版仍为五项质检全过。

## 下载

| 文件 | 大小 | SHA256 |
|---|---|---|
| `paper2zh-v1.2.2-win-x64-lite.zip` | 12.6 MB | `F7E8BD6C1202B024FE87EAEB9CEF0604872EB976B83ECDD240CDB57D8C695634` |
| `paper2zh-v1.2.2-win-x64-full.zip` | 1.94 GB | `20A3AC65F895A556E1AC936B20D8474A8EE354C8C10CC0D772A1F8D4A2D403D3` |

## 说明

- 工具**没有自动升级**；升级只换脚本，不动已有安装里的依赖、模型和翻译结果；
- 从 1.2.1 升级时，按 `AI-INSTALL.md` 的「升级旧版本」操作即可；
- 如果你已经下载过旧的 1.2.2 包，请用上面的新哈希替换。
