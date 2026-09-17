// V11.336 前端付款方式下拉 真实代码断言(node 跑 app.js 里的真实函数, 非重写)
const fs = require('fs');
const src = fs.readFileSync(process.argv[2] || 'static/app.js', 'utf8');
function grab(name){
  const re = new RegExp('(?:^|\\n)((?:const ' + name + '=|async function ' + name + '\\(|function ' + name + '\\()[\\s\\S]*?\\n}\\n)');
  const m = src.match(re);
  if(!m) throw new Error('未找到函数 '+name);
  return m[1];
}
const code = grab('EMG_PAY_PRESET').replace(/^const EMG_PAY_PRESET=/, 'var EMG_PAY_PRESET=')
  + grab('emgPayOpts') + grab('emgPaySel') + grab('emgPayVal') + grab('emgInquirySave');
// DOM 桩
const els = {};
function id(k){ return els[k]; }
function esc(s){ return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;'); }
function alert(m){ throw new Error('alert:'+m); }
let ok=[],bad=[];
function chk(n,c,x){ (c?ok:bad).push(n); console.log((c?'  ✅ ':'  ❌ ')+n+(c?'':' | '+JSON.stringify(x))); }
eval(code);

// 场景1: 预设值回显(月结30天)
els.emgInqPaySel={value:'月结30天'}; els.emgInqPay={value:'月结30天',style:{}};
emgPaySel(true);
chk('预设值: 自定义输入框隐藏', els.emgInqPay.style.display==='none' , els.emgInqPay.style);
chk('取值=月结30天', emgPayVal()==='月结30天', emgPayVal());
const opt1 = emgPayOpts('月结30天');
chk('选项渲染: 月结30天 selected', /value="月结30天" selected/.test(opt1), opt1);
chk('选项渲染: 含"货到付款（现结）"', opt1.includes('货到付款（现结）'), opt1);
chk('选项渲染: 含 自定义…', opt1.includes('自定义…'), opt1);

// 场景2: 历史自定义文本(承兑汇票) → 选中自定义项并回填输入框
const opt2 = emgPayOpts('承兑汇票');
chk('自定义值: 选中 __custom__', /value="__custom__" selected/.test(opt2), opt2);
els.emgInqPaySel={value:'__custom__'}; els.emgInqPay={value:'承兑汇票',style:{}};
emgPaySel(true);
chk('自定义值: 输入框显示', els.emgInqPay.style.display==='', els.emgInqPay.style);
chk('自定义值: 取值=承兑汇票', emgPayVal()==='承兑汇票', emgPayVal());

// 场景3: 空值默认 → 货到付款选中, 月结类判定
const opt3 = emgPayOpts('');
chk('空值默认选中货到付款', /value="货到付款" selected/.test(opt3), opt3);
chk('月结判定: 月结30天→月结', '月结30天'.includes('月结')===true);
chk('月结判定: 货到付款→现结', '货到付款'.includes('月结')===false);

// 场景4: 保存时 payload 带 settle_type(经 emgInquirySave 组装)
let sent=null;
global.api = async (p,d)=>{ sent={p,d:JSON.parse(d.body)}; return {success:true,message:'ok'}; };
global.toast=()=>{};
global.closeMod=()=>{}; global.loadEmergency=()=>{};
els.emgInqSup={value:'测试供应商'}; els.emgInqAmt={value:'1000'}; els.emgInqRate={value:'13'};
els.emgInqValid={value:''}; els.emgInqDays={value:'7'};
els.emgInqPaySel={value:'月结30天'}; els.emgInqPay={value:'月结30天',style:{}};
window={};
(async()=>{
  await emgInquirySave(1);
  chk('保存payload: pay_method=月结30天', sent && sent.d.pay_method==='月结30天', sent);
  chk('保存payload: settle_type=月结', sent && sent.d.settle_type==='月结', sent);
  els.emgInqPaySel={value:'货到付款'}; els.emgInqPay={value:'货到付款',style:{}};
  await emgInquirySave(1);
  chk('保存payload: 货到付款→settle_type=现结', sent && sent.d.settle_type==='现结', sent);
  console.log('\n=== '+(bad.length?('失败 '+bad.length+' 项'):'全部通过 ('+ok.length+' 项)')+' ===');
  process.exit(bad.length?1:0);
})();
