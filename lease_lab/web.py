INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>租约语义演算器</title>
<style>
:root{--bg:#f6f7fb;--card:#fff;--line:#d9deea;--ink:#172033;--muted:#65708a;--ok:#0f7a45;--bad:#b42318;--warn:#a15c00;--blue:#2447d8}
*{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
header{padding:22px 26px 12px;background:#101b40;color:white} h1{margin:0 0 4px;font-size:26px} header p{margin:0;color:#c8d1ff}
main{padding:18px 24px 40px}.toolbar{display:flex;gap:10px;flex-wrap:wrap;align-items:end;margin-bottom:14px}
.field{display:flex;flex-direction:column;gap:4px}.field label{color:var(--muted);font-size:12px}select,input,textarea,button{font:inherit;border:1px solid var(--line);border-radius:8px;padding:8px;background:white}
button{cursor:pointer;background:var(--blue);color:white;border-color:var(--blue);padding:8px 12px}button.secondary{background:white;color:var(--ink)}
button.danger{background:#7a271a;border-color:#7a271a}.grid{display:grid;grid-template-columns:1.1fr 1fr 1fr 1.1fr;gap:12px}.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px;box-shadow:0 1px 3px #1d293914}
.card h2{font-size:16px;margin:0 0 10px}.kv{display:grid;grid-template-columns:auto 1fr;gap:5px 10px;margin:6px 0}.kv b{font-weight:650}.muted{color:var(--muted)}.ok{color:var(--ok);font-weight:700}.bad{color:var(--bad);font-weight:700}.warn{color:var(--warn);font-weight:700}
.lease{border-top:1px solid var(--line);margin-top:10px;padding-top:8px}.actions{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:14px 0}
textarea{min-height:84px;width:100%;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}table{border-collapse:collapse;width:1000px;max-width:100%;background:white}th,td{border-bottom:1px solid var(--line);padding:7px;text-align:left;vertical-align:top}th{font-size:12px;color:var(--muted);position:sticky;top:0;background:white}
.tablewrap{overflow:auto;background:white;border:1px solid var(--line);border-radius:12px;padding:4px;margin-top:14px}.pill{display:inline-block;border-radius:999px;padding:2px 8px;font-size:12px;font-weight:700}.pill.ok{background:#dcf7e8}.pill.bad{background:#feeeec}.pill.warn{background:#fff3d8}
#message{margin:10px 0;padding:10px 12px;border-radius:8px;white-space:pre-wrap}.hidden{display:none}
@media(max-width:1100px){.grid{grid-template-columns:1fr 1fr}.actions{grid-template-columns:1fr}}
</style>
</head>
<body>
<header><h1>租约语义演算器</h1><p>显式 tick 推进；不可变事件推导租约、fencing token、暂停/分区视图与资源侧裁决。</p></header>
<main>
<section class="toolbar">
 <div class="field"><label>场景</label><select id="scenario"></select></div>
 <div class="field"><label>分支</label><select id="branch"></select></div>
 <button id="refresh">刷新</button>
 <div class="field"><label>新场景名称</label><input id="newScenario" placeholder="新虚拟时钟场景"></div><button id="createScenario">创建场景</button>
 <div class="field"><label>Fork 名称</label><input id="forkName" placeholder="例如：提前续约"></div>
 <button id="fork">从当前尾事件 Fork</button>
 <button id="exportBtn" class="secondary">导出</button><button id="importBtn" class="secondary">导入下方 JSON</button>
</section>
<div id="message" class="hidden"></div>
<section class="grid">
 <div class="card"><h2>协调端</h2><div id="coordinator"></div></div>
 <div class="card"><h2>客户端 A</h2><div id="clientAlpha"></div></div>
 <div class="card"><h2>客户端 B</h2><div id="clientBeta"></div></div>
 <div class="card"><h2>资源侧判断</h2><div id="resource"></div></div>
</section>
<section class="actions">
 <div class="card">
  <h2>追加动作（乐观版本号）</h2>
  <div class="toolbar">
   <div class="field"><label>动作</label><select id="actionType">
    <option>tick</option><option>create_resource</option><option>create_client</option><option>acquire</option><option>renew</option><option>write</option>
    <option>pause</option><option>resume</option><option>partition</option><option>recover</option>
   </select></div>
   <div class="field"><label>资源</label><select id="resourceSelect"></select><input id="newResourceId" placeholder="新资源 ID"></div>
   <div class="field"><label>客户端</label><select id="clientSelect"></select><input id="newClientId" placeholder="新客户端 ID"></div>
   <div class="field"><label>TTL / tick 数</label><input id="numberValue" type="number" value="4" min="1"></div>
  </div>
  <div class="field"><label>写入值 / 名称</label><input id="writeValue" placeholder="value 或 name"></div>
  <div class="field" style="margin:8px 0"><label>幂等键（可空）</label><input id="requestId" placeholder="request id"></div>
  <button id="append">追加到版本 <span id="expectedVersion">0</span> 之后</button>
 </div>
 <div class="card">
  <h2>原始 payload / 导入导出</h2>
  <textarea id="payload" spellcheck="false">{"ticks":1}</textarea>
  <p class="muted">可直接编辑 JSON。409 响应会显示当前分支尾事件，刷新后可基于新版本重试。</p>
 </div>
</section>
<section class="tablewrap"><table><thead><tr><th>#</th><th>tick</th><th>动作</th><th>客户端</th><th>资源</th><th>结果</th><th>payload</th><th>客户端观测</th></tr></thead><tbody id="events"></tbody></table></section>
</main>
<script>
const $=id=>document.getElementById(id);
let detail=null;
const esc=v=>String(v??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const reason={lease_expired:"lease expired（恰好到期即失效）",not_holder:"not holder（当前非持有者）",stale_token:"stale token（资源侧拒绝旧 fencing token）",already_paused:"already paused",not_paused:"not paused",already_partitioned:"already partitioned",not_partitioned:"not partitioned"};
function msg(text,bad=false){const m=$("message");m.textContent=text;m.className=bad?"bad":"ok";m.style.background=bad?"#feeeec":"#dcf7e8";m.style.border="1px solid "+(bad?"#f0a8a0":"#9bdcb8")}
async function api(url,options={}){const res=await fetch(url,{headers:{"Content-Type":"application/json"},...options});const body=await res.text();const data=body?JSON.parse(body):{};if(!res.ok)throw Object.assign(new Error(JSON.stringify(data.detail??data)),{status:res.status,data});return data}
async function loadScenarios(){const list=await api("/api/scenarios");$("scenario").innerHTML=list.map(s=>`<option value="${s.id}">${esc(s.name)}</option>`).join("");if(!list.length)return;if(!list.some(s=>s.id===$("scenario").value))$("scenario").value=list[0].id;await loadBranches()}
async function loadBranches(){const sid=$("scenario").value;const data=await api(`/api/scenarios/${sid}`);$("branch").innerHTML=data.branches.map(b=>`<option value="${b.id}">${esc(b.name)} #${b.tail_seq}</option>`).join("");await loadBranch()}
async function loadBranch(){const bid=$("branch").value;detail=await api(`/api/branches/${bid}`);$("expectedVersion").textContent=detail.view.last_event_seq;render();await refreshSelectors()}
function kv(k,v,cls=""){return `<div><b>${k}</b></div><div class="${cls}">${v}</div>`}
function statusPill(active,expiredText="已过期"){return active?'<span class="pill ok">active</span>':`<span class="pill bad">${expiredText}</span>`}
function renderLeases(view,scope){const leases=Object.entries(scope.leases);if(!leases.length)return '<p class="muted">无租约</p>';return leases.map(([rid,l])=>{const name=scope.resources[rid]?.name??rid;return `<div class="lease">${kv("资源",esc(name))}${kv("holder",esc(l.holder_id))}${kv("token",l.token)}${kv("生效区间",`${l.acquired_at} ≤ t < ${l.expires_at}`)}${kv("状态",statusPill(l.active),"ok")}</div>`}).join("")}
function render(){const v=detail.view;$("coordinator").innerHTML=`${kv("协调端时间 t",v.time)}${kv("下一个 token",v.next_token)}${kv("分支版本",v.last_event_seq)}${renderLeases(v,v)}`;
$("resource").innerHTML=Object.values(v.resources).map(r=>{const l=v.leases[r.id];const verdict=(detail.events.at(-1)?.reject_reason==="stale_token")?'<span class="pill bad">拒绝 token 1：已接受 token 2</span>':'等待写入';return `${kv("资源",esc(r.name))}${kv("当前值",esc(r.value??"∅"))}${kv("已接受 token",r.accepted_token??"∅")}${kv("最后写入者",esc(r.updated_by??"∅"))}<div class="lease">${kv("资源侧裁决",verdict)}</div>`}).join("")||'<p class="muted">无资源</p>';
$("clientAlpha").innerHTML=renderClient("cli_alpha");$("clientBeta").innerHTML=renderClient("cli_beta");
$("events").innerHTML=detail.events.map(e=>`<tr><td>${e.seq}</td><td>${e.at_tick}</td><td>${esc(e.type)}</td><td>${esc(e.client_id??"")}</td><td>${esc(e.resource_id??"")}</td><td>${e.success?'<span class="pill ok">accepted</span>':`<span class="pill bad">${esc(reason[e.reject_reason]??e.reject_reason)}</span>`}</td><td><code>${esc(JSON.stringify(e.payload))}</code></td><td>${esc(e.client_time??"")}</td></tr>`).join("");
const last=detail.events.at(-1);if(last&&!last.success&&last.reject_reason==="stale_token")msg(`事件 #${last.seq}：旧持有者的 token ${last.payload.token} 晚提交；资源侧已看到更新 token，判定为 stale token。`,true)}
function renderClient(id){const c=detail.view.clients[id];if(!c)return '<p class="muted">客户端尚未创建</p>';const flags=[c.client.paused?"暂停":"运行",c.client.partitioned?"网络分区":"已连接"].map(x=>`<span class="pill warn">${x}</span>`).join(" ");return `${kv("客户端",esc(c.client.name))}${kv("观测时间",c.observed_time)}${kv("状态",flags)}${kv("可见事件截止",c.visible_event_seq)}${renderLeases(detail.view,c)}`+Object.values(c.resources).map(r=>`${kv("可见资源值",esc(r.value??"∅"))}${kv("可见 token",r.accepted_token??"∅")}`).join("")}
async function refreshSelectors(){const v=detail.view;$("resourceSelect").innerHTML=Object.values(v.resources).map(r=>`<option value="${r.id}">${esc(r.name)}</option>`).join("");$("clientSelect").innerHTML=Object.values(v.clients).map(c=>`<option value="${c.client.id}">${esc(c.client.name)}</option>`).join("")}
function makePayload(){const type=$("actionType").value,n=Number($("numberValue").value),name=$("writeValue").value;if(type==="tick")return{ticks:n};if(type==="create_resource")return{name:name||"新资源"};if(type==="create_client")return{name:name||"新客户端"};if(type==="acquire"||type==="renew")return{ttl:n};if(type==="write"){const cid=$("clientSelect").value,lease=detail.view.leases[$("resourceSelect").value];return{token:lease&&lease.holder_id===cid?lease.token:1,value:name||"late write"}}return{}}
$("actionType").onchange=()=>{$("payload").value=JSON.stringify(makePayload(),null,2)};
$("append").onclick=async()=>{let payload;try{payload=JSON.parse($("payload").value)}catch(e){return msg("payload 不是合法 JSON",true)}const type=$("actionType").value;const clientId=type==="create_client"?$("newClientId").value.trim():["tick","pause","resume","partition","recover","acquire","renew","write"].includes(type)?$("clientSelect").value:null;const resourceId=type==="create_resource"?$("newResourceId").value.trim():["acquire","renew","write"].includes(type)?$("resourceSelect").value:null;const body={expected_version:detail.view.last_event_seq,request_id:$("requestId").value||null,type,client_id:clientId,resource_id:resourceId,payload};try{const r=await api(`/api/branches/${$("branch").value}/actions`,{method:"POST",body:JSON.stringify(body)});msg(r.idempotent?"幂等命中：返回原事件 #"+r.event.seq:"已追加事件 #"+r.event.seq);$("requestId").value="";$("newResourceId").value="";$("newClientId").value="";await loadBranch()}catch(e){if(e.status===409)msg("追加失败（409）。当前分支尾事件：\n"+JSON.stringify(e.data.detail.current_tail??e.data.detail,null,2),true);else msg(String(e.message),true)}};
$("createScenario").onclick=async()=>{await api("/api/scenarios",{method:"POST",body:JSON.stringify({name:$("newScenario").value||"新场景"})});await loadScenarios()};
$("fork").onclick=async()=>{const sid=$("scenario").value,bid=$("branch").value;await api(`/api/scenarios/${sid}/branches`,{method:"POST",body:JSON.stringify({name:$("forkName").value||("fork @ "+detail.view.last_event_seq),parent_branch_id:bid})});await loadBranches()};
$("exportBtn").onclick=async()=>{const data=await api(`/api/scenarios/${$("scenario").value}/export`);$("payload").value=JSON.stringify(data,null,2)};
$("importBtn").onclick=async()=>{try{await api("/api/import",{method:"POST",body:$("payload").value});msg("导入完成");await loadScenarios()}catch(e){msg(String(e.message),true)}};
$("refresh").onclick=loadBranch;$("scenario").onchange=loadBranches;$("branch").onchange=loadBranch;
loadScenarios().catch(e=>msg(String(e),true));
</script>
</body></html>
"""
