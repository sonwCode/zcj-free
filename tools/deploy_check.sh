#!/bin/bash
# 校验云端 /opt/turb-gpt-register 与仓库 tools/deploy_manifest.txt 是否一致。
# 用法（云端）： bash tools/deploy_check.sh [项目根目录]
set -u
ROOT="${1:-/opt/turb-gpt-register}"
MANIFEST="$(dirname "$0")/deploy_manifest.txt"
fail=0
while read -r hash path; do
  [ -z "$hash" ] && continue
  target="$ROOT/$path"
  if [ ! -f "$target" ]; then
    echo "缺失  $path"; fail=$((fail+1)); continue
  fi
  actual=$(md5sum "$target" | cut -c1-8)
  if [ "$actual" != "$hash" ]; then
    echo "不一致 $path  期望=$hash 实际=$actual"; fail=$((fail+1))
  fi
done < "$MANIFEST"
if [ "$fail" -eq 0 ]; then
  echo "OK：云端与清单一致（$(grep -c . "$MANIFEST") 个文件）"
else
  echo "发现 $fail 处差异"
  exit 1
fi
