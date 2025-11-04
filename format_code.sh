#!/bin/bash

set -euo pipefail

echo "🧹 开始代码格式化..."

echo "🔧 检查并安装依赖包..."
python -m pip install --upgrade pip >/dev/null
python -m pip install autoflake docformatter isort black flake8 >/dev/null

echo "📦 依赖包安装完成"

# 定义要格式化的目标文件夹和文件
TARGETS=("src" "scripts" "main.py")
EXISTING_TARGETS=()

for target in "${TARGETS[@]}"; do
    if [ -e "$target" ]; then
        EXISTING_TARGETS+=("$target")
    else
        echo "⚠️ 跳过不存在的路径: $target"
    fi
done

if [ ${#EXISTING_TARGETS[@]} -eq 0 ]; then
    echo "❌ 未找到任何可格式化的路径。"
    exit 1
fi

echo "📁 格式化目标: ${EXISTING_TARGETS[*]}"
echo ""

# 删除未使用导入和变量（非侵入但有效）
echo "1️⃣ 删除未使用的导入和变量..."
python -m autoflake -r --in-place --remove-unused-variables --remove-all-unused-imports --ignore-init-module-imports "${EXISTING_TARGETS[@]}"

# 修复 docstring 的标点、首字母等格式
echo "2️⃣ 格式化文档字符串..."
python -m docformatter -r -i --wrap-summaries=88 --wrap-descriptions=88 --make-summary-multi-line "${EXISTING_TARGETS[@]}"

# 自动排序导入
echo "3️⃣ 排序导入语句..."
python -m isort "${EXISTING_TARGETS[@]}"

# 自动格式化（处理长行、函数参数、f字符串等）
echo "4️⃣ 格式化代码..."
python -m black "${EXISTING_TARGETS[@]}"

# 最后静态检查（非修复）
echo "5️⃣ 静态代码检查..."
python -m flake8 "${EXISTING_TARGETS[@]}"

echo ""
echo "✅ 代码格式化完成！"
echo "📊 已处理的目标: ${EXISTING_TARGETS[*]}"
