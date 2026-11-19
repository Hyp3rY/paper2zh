# paper2zh v1.2.1

## 中文

修复跨公式块拼接导致的阅读顺序问题。

- 当“文本 → 公式 → 文本”连续出现时，`stitch_continuations` 不再跨过公式块拼接前后段落；
- 此前会把几段文字合并成一大段，公式被挤到整段之后，公式密集型论文受影响明显；
- 新增回归测试 `tests/test_stitch.py`，覆盖“不跨公式拼接”和“普通续句仍然拼接”两种情况。

建议公式较多的论文升级。没有新增功能，也没有改动翻译流程。

## English

Fix reading-order corruption caused by stitching text across display equations.

- `stitch_continuations` no longer merges paragraphs across an `equation` block;
- Previously, several text blocks could collapse into one long paragraph and the equations
  were pushed after it, which was especially visible in equation-heavy papers;
- Added `tests/test_stitch.py`, covering both "do not stitch across an equation" and
  "ordinary continuation still stitches".

Recommended upgrade for equation-heavy papers. No new features; translation flow unchanged.
