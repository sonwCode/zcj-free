
set -e
cd /opt/turb-gpt-register

# 备份
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
mkdir -p deploy-backups
cp core/cloakbrowser_registration.py deploy-backups/cloak-pre-job175-$TIMESTAMP.py
cp core/browser_traffic.py deploy-backups/traffic-pre-job175-$TIMESTAMP.py
echo "备份完成: $TIMESTAMP"

# 拉取远程分支
echo "拉取远程代码..."
git fetch origin deploy/all-local-changes

# 检出目标文件
echo "检出修复文件..."
git checkout origin/deploy/all-local-changes -- core/cloakbrowser_registration.py core/browser_traffic.py

# 验证
echo ""
echo "=== 验证修复点 ==="

if grep -q 'def snapshot_without_browser' core/browser_traffic.py; then
  echo '✓ snapshot_without_browser 方法已部署'
else
  echo '✗ snapshot_without_browser 未找到'
  exit 1
fi

if grep -q 'snapshot_without_browser()' core/cloakbrowser_registration.py; then
  echo '✓ 成功路径调用已部署'
else
  echo '✗ 成功路径调用未找到'
  exit 1
fi

if grep -q 'exclude_codes=used_otps' core/cloakbrowser_registration.py; then
  echo '✓ exclude_codes 已部署'
else
  echo '✗ exclude_codes 未找到'
  exit 1
fi

# 编译检查
echo ""
echo "=== Python 语法检查 ==="
python3 -m py_compile core/cloakbrowser_registration.py
python3 -m py_compile core/browser_traffic.py
echo '✓ 语法检查通过'

# 重启
echo ""
echo "=== 重启服务 ==="
systemctl restart turb-gpt-register
sleep 3
systemctl status turb-gpt-register --no-pager | head -15

echo ""
echo "✓ 部署完成"
echo "备份: deploy-backups/*-$TIMESTAMP.py"
