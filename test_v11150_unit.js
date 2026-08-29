// V11.150 单元测试: 防连点守卫 + GET缓存 纯逻辑验证 (node 直接跑)
// 用法: node test_v11150_unit.js
const assert = require('assert');

// ---- 从 index.html 提取的纯逻辑复刻(与页面代码一致) ----
const __openGuard = new Map();
function __guardOpen(key){const now=Date.now();if(now-(__openGuard.get(key)||0)<600)return true;__openGuard.set(key,now);return false}

// sessionStorage 模拟
const _store = {};
const sessionStorage = {
  getItem(k){return Object.prototype.hasOwnProperty.call(_store,k)?_store[k]:null},
  setItem(k,v){_store[k]=String(v)},
  removeItem(k){delete _store[k]},
  key(i){return Object.keys(_store)[i]||null},
  get length(){return Object.keys(_store).length},
};
const __CACHE_TTL=25000;
const __CACHE_PREFIX='v11150';
const __CACHE_SKIP=['/me','/public-url','/approvals/pending','/notifications','/search','/logs','/users'];
function __cacheGet(url){try{const raw=sessionStorage.getItem(__CACHE_PREFIX+url);if(!raw)return null;const c=JSON.parse(raw);if(Date.now()-c.t<__CACHE_TTL)return c.d}catch(e){}return null}
function __cacheSet(url,d){try{sessionStorage.setItem(__CACHE_PREFIX+url,JSON.stringify({t:Date.now(),d}))}catch(e){}}
function __cacheClear(){try{const ks=[];for(let i=0;i<sessionStorage.length;i++){const k=sessionStorage.key(i);if(k&&k.startsWith(__CACHE_PREFIX))ks.push(k)}ks.forEach(k=>sessionStorage.removeItem(k))}catch(e){}}

let pass=0, fail=0;
function t(name, fn){try{fn();pass++;console.log('  ✅',name)}catch(e){fail++;console.log('  ❌',name,'→',e.message)}}

(async()=>{
console.log('[1] 防连点守卫 __guardOpen');
t('第一次点击放行', ()=>{assert.strictEqual(__guardOpen('inq:5'), false)});
t('600ms内重复点击被拦', ()=>{assert.strictEqual(__guardOpen('inq:5'), true)});
t('不同入口互不影响', ()=>{assert.strictEqual(__guardOpen('inq:6'), false)});
t('不同单据号互不影响', ()=>{assert.strictEqual(__guardOpen('ord:5'), false)});
await new Promise(r=>setTimeout(r,620));
t('超过600ms后再次放行', ()=>{assert.strictEqual(__guardOpen('inq:5'), false)});

console.log('[2] GET列表缓存 __cacheGet/__cacheSet/__cacheClear');
t('未命中返回null', ()=>{assert.strictEqual(__cacheGet('/inquiries'), null)});
__cacheSet('/inquiries', [{id:1,inq_no:'XJ-1'}]);
t('写入后命中且值正确', ()=>{assert.deepStrictEqual(__cacheGet('/inquiries'), [{id:1,inq_no:'XJ-1'}])});
t('跳过列表识别正确(api层谓词)', ()=>{const isSkip=u=>__CACHE_SKIP.some(s=>u.startsWith(s));assert.strictEqual(isSkip('/me'),true);assert.strictEqual(isSkip('/approvals/pending'),true);assert.strictEqual(isSkip('/inquiries'),false);assert.strictEqual(isSkip('/inquiries/5'),false);assert.strictEqual(isSkip('/orders'),false)});
t('缓存清空后失效', ()=>{__cacheClear();assert.strictEqual(__cacheGet('/inquiries'), null)});
__cacheSet('/orders',[1,2,3]);__cacheSet('/inquiries',[4,5]);
t('清空只清本前缀key', ()=>{__cacheClear();assert.strictEqual(__cacheGet('/orders'), null);assert.strictEqual(__cacheGet('/inquiries'), null);assert.strictEqual(sessionStorage.length,0)});

// TTL 过期验证: 手动把时间戳改旧
__cacheSet('/inquiries',[9]);
const raw=JSON.parse(sessionStorage.getItem(__CACHE_PREFIX+'/inquiries'));
raw.t=Date.now()-30000; sessionStorage.setItem(__CACHE_PREFIX+'/inquiries',JSON.stringify(raw));
t('超过25秒TTL后缓存失效', ()=>{assert.strictEqual(__cacheGet('/inquiries'), null)});

console.log(`\n结果: ${pass} 通过, ${fail} 失败`);
process.exit(fail?1:0);
})();
