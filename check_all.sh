#!/bin/bash
# 改动后一键检查: 语法 + 回归 + 链接
cd "$(dirname "$0")"
echo "═══ 1. 后端检查 ═══"
.venv/bin/python check_code.py 2>&1 | tail -1
echo "═══ 2. 前端JS语法 ═══"
node -e "
const fs=require('fs');
const html=fs.readFileSync('templates/index.html','utf8');
const ss=[...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m=>m[1]);
let e=0; ss.forEach((s,i)=>{try{new Function(s)}catch(x){e++;console.log('JS块'+i+':',x.message.slice(0,80))}});
console.log(e?('❌ JS错误块:'+e):'✅ JS语法通过');
"
echo "═══ 3. 全链路回归 ═══"
.venv/bin/python test_regression_v1197.py 2>&1 | grep -E "^结果|❌"
echo "═══ 4. 公网链接 ═══"
PU=$(cat data/public_url.txt 2>/dev/null)
echo "当前地址: $PU"
curl -s -m 10 -o /dev/null -w "公网HTTP: %{http_code}\n" "$PU"
