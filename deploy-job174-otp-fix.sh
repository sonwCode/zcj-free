#!/bin/bash
set -e

COMMIT_HASH="87f35c7"
BACKUP_DIR="/opt/turb-gpt-register/deploy-backups"
SERVICE_NAME="turb-gpt-register"

echo "=== Job 174 OTP Loop Fix Deployment ==="
echo "Commit: $COMMIT_HASH"
echo "移除重复 OTP 检查分支，修复 Remail 返回旧码时的无限重发循环"
echo ""

# 备份
echo "创建部署前备份..."
mkdir -p "$BACKUP_DIR"
cp /opt/turb-gpt-register/core/cloakbrowser_registration.py \
   "$BACKUP_DIR/cloak-otp-loop-pre-$COMMIT_HASH.py"

# 部署
echo "部署修复文件..."
cd /opt/turb-gpt-register
git fetch origin deploy/all-local-changes
git checkout $COMMIT_HASH core/cloakbrowser_registration.py

# 验证
echo "运行静态检查..."
python3 -m py_compile core/cloakbrowser_registration.py

# 重启
echo "重启服务..."
sudo systemctl restart $SERVICE_NAME
sleep 2
sudo systemctl status $SERVICE_NAME --no-pager

echo ""
echo "✓ 部署完成"
echo "监控: sudo journalctl -u $SERVICE_NAME -f --since '1 min ago'"
echo "回滚: cp $BACKUP_DIR/cloak-otp-loop-pre-$COMMIT_HASH.py core/cloakbrowser_registration.py && sudo systemctl restart $SERVICE_NAME"
