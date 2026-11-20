# paper2zh v1.2.2

## 中文

输出文件改成中文名。

- `finish` 仍先生成内部的 `zh.md` / `bilingual.md`，等质检、Word 导出完成后，自动改名为
  **`中文版.md`** / **`中英对照.md`**；
- `先读我.txt` 里的文件名同步更新；`finish --docx` / `export` 生成的 Word 也改为 `中文版.docx` / `中英对照.docx`；
- 旧目录里的 `zh.md` / `bilingual.md` 仍可被质检读取，不影响已有产物；
- 新增 `tests/test_output_names.py`，覆盖改名和重复执行覆盖。

另外，`AI-INSTALL.md` 增加了「升级旧版本」一节：工具没有自动升级，按说明下载最新 lite 包、解压到新目录、重新跑安装即可；旧配置和已翻好的内容不会被覆盖。

## English

Chinese output filenames.

- `finish` still renders `zh.md` / `bilingual.md` internally, then renames them to
  `中文版.md` / `中英对照.md` after QA and Word export;
- `先读我.txt` and the post-hoc Word export are updated accordingly;
- old workdirs remain readable through a fallback;
- added `tests/test_output_names.py` for the rename path.

`AI-INSTALL.md` now includes an upgrade section; there is no auto-update.
