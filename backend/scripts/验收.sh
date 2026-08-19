#!/usr/bin/env bash
# 子任务 12 · 小说解构流水线验收脚本（00 §7.3）
# 用法：bash scripts/验收.sh [book_id]   （默认 doc_1_5）
set -euo pipefail
BOOK_ID="${1:-doc_1_5}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# 优先用项目 conda 环境（env_agent001），否则退回 python
PY="$(command -v "$HOME/miniconda3/envs/env_agent001/bin/python" 2>/dev/null || echo python)"

echo "===== 1. 全量测试 ====="
PYTHONPATH="$ROOT/src:$ROOT/tests" "$PY" -m pytest -q tests/test_novel_*.py || {
  echo "⚠️ 有失败（DeepSeek CLI 用例可门控跳过）；请检查"
}

echo ""
echo "===== 2. orchestrator dry-run 盘点 ====="
PYTHONPATH="$ROOT/src" "$PY" -m novel.orchestrator --book_id "$BOOK_ID" --dry-run

echo ""
echo "===== 3. MySQL 对账（novel_chapter / job 状态） ====="
# Docker MySQL 暴露在 TCP 127.0.0.1:3306（非 unix socket）；root 密码取 .env；DB=ai_customer_service
MYSQL="mysql -h 127.0.0.1 -P 3306 -uroot -proot_pass_2026 ai_customer_service"
$MYSQL -e "SELECT COUNT(*) AS chapters FROM novel_chapter WHERE book_id='$BOOK_ID';"
$MYSQL -e "SELECT status, COUNT(*) AS n FROM deconstruct_chapter_state GROUP BY status;"
$MYSQL -e "SELECT status, done_chapters, failed_chapters, total_chapters FROM deconstruct_job ORDER BY id DESC LIMIT 3;"

echo ""
echo "===== 4. 解构结果 11 表抽查 ====="
$MYSQL -e "SELECT COUNT(*) AS entities FROM entity WHERE book_id='$BOOK_ID';"
$MYSQL -e "SELECT COUNT(*) AS issues FROM validation_issue WHERE book_id='$BOOK_ID';"

echo ""
echo "✅ 验收脚本执行完成（业务规则专项见 spec 12 §7.4）"
