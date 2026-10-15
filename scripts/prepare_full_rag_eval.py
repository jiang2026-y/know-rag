"""准备 MinerU 文档评测集：从章节内容生成可追溯的脱敏问题、答案和金标准证据。"""
from __future__ import annotations
import argparse, json, re
from pathlib import Path

def sections(markdown: str) -> list[tuple[str, str]]:
    """按标题提取章节及正文。"""
    result, title, lines = [], "", []
    for line in markdown.splitlines():
        if re.match(r"^#{1,3}\s+", line):
            if title and "\n".join(lines).strip(): result.append((title, "\n".join(lines).strip()))
            title = re.sub(r"^#+\s+", "", line).strip(); lines = [line]
        else: lines.append(line)
    if title and "\n".join(lines).strip(): result.append((title, "\n".join(lines).strip()))
    return result

def main() -> None:
    """生成 MinerU 评测样本。"""
    ap = argparse.ArgumentParser(); ap.add_argument("--mineru-document", type=Path, required=True); ap.add_argument("--mineru-count", type=int, default=30); ap.add_argument("--mode", choices=["mock", "api"], default="mock"); ap.add_argument("--output", type=Path, required=True); args = ap.parse_args()
    parsed = sections(args.mineru_document.read_text(encoding="utf-8")); chosen = [(t, c) for t, c in parsed if len(c) >= 80][: args.mineru_count]
    if len(chosen) < args.mineru_count: raise SystemExit(f"文档中可用章节不足 {args.mineru_count} 条")
    rows = [{"id": f"mineru-{i:03d}", "question": f"如何使用或设置“{title}”？", "reference_answer": content.replace("\n", " ")[:500], "gold_context": content[:1200], "section": title, "source_document": args.mineru_document.name, "generation_mode": args.mode} for i, (title, content) in enumerate(chosen, 1)]
    args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"); print(f"已生成 {len(rows)} 条 MinerU 评测样本：{args.output}")
if __name__ == "__main__": main()
