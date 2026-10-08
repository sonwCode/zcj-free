#!/bin/bash
set -e

TARGET_COMMIT="87f35c7"
BACKUP_DIR="/opt/turb-gpt-register/deploy-backups"
SERVICE_NAME="turb-gpt-register"

echo "=== 部署 Job 173-175 修复 ==="
echo "目标提交: $TARGET_COMMIT"
echo ""
echo "修复列表:"
echo "  37de796: 防止成功路径阻塞在 tracker.stop()"
echo "  2c173e6: 计算 worker 超时时包含 OTP 重试时间"
echo "  87f35c7: 移除重复 OTP 检查导致的重发循环"
echo ""

# 备份
echo "创建部署前备份..."
mkdir -p "$BACKUP_DIR"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
cp /opt/turb-gpt-register/core/cloakbrowser_registration.py \
   "$BACKUP_DIR/cloak-pre-$TARGET_COMMIT-$TIMESTAMP.py"
cp /opt/turb-gpt-register/core/browser_traffic.py \
   "$BACKUP_DIR/traffic-pre-$TARGET_COMMIT-$TIMESTAMP.py"

# 部署
echo "拉取最新代码..."
cd /opt/turb-gpt-register
git fetch origin deploy/all-local-changes
git checkout $TARGET_COMMIT -- \
  core/cloakbrowser_registration.py \
  core/browser_traffic.py

# 验证
echo "运行静态检查..."
python3 -m py_compile \
  core/cloakbrowser_registration.py \
  core/browser_traffic.py

echo "检查关键修复点..."
grep -q "snapshot_without_browser" core/cloakbrowser_registration.py || {
  echo "❌ snapshot_without_browser 未找到"
  exit 1
}
grep -q "exclude_codes=used_otps" core/cloakbrowser_registration.py || {
  echo "❌ exclude_codes 未找到"
  exit 1
}
grep -q "def snapshot_without_browser" core/browser_traffic.py || {
  echo "❌ snapshot_without_browser 方法未找到"
  exit 1
}

echo "✓ 所有修复点已确认"

# 重启
echo "重启服务..."
sudo systemctl restart $SERVICE_NAME
sleep 3
sudo systemctl status $SERVICE_NAME --no-pager

echo ""
echo "✓ 部署完成"
echo ""
echo "修复内容:"
echo "  1. 成功路径使用非阻塞快照，避免 tracker.stop() 卡死"
echo "  2. Worker 超时预算 = 基础超时 + (OTP等待 × 3)"
echo "  3. 移除重复 OTP 检查，防止 Remail 返回旧码时无限重发"
echo ""
echo "监控命令:"
echo "  sudo journalctl -u $SERVICE_NAME -f --since '1 min ago'"
echo ""
echo "回滚命令:"
echo "  cp $BACKUP_DIR/cloak-pre-$TARGET_COMMIT-$TIMESTAMP.py core/cloakbrowser_registration.py"
echo "  cp $BACKUP_DIR/traffic-pre-$TARGET_COMMIT-$TIMESTAMP.py core/browser_traffic.py"
echo "  sudo systemctl restart $SERVICE_NAME"
