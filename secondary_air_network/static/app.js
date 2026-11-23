"use strict";
const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="network-token"]').content;
const maxNodes = Number(document.querySelector('meta[name="network-max-nodes"]').content);
const maxEdges = Number(document.querySelector('meta[name="network-max-edges"]').content);
const maxImportMiB = 64;
let model, result = null, selected = null, currentView = "model", cases = [], sweepResult = null, vortexResult = null, gridResult = null, hasEdits = false;
const kindNames = {orifice:"オリフィス", leak:"等価漏れ", pipe:"配管", resistance:"局所抵抗", labyrinth:"ラビリンス（完全散逸）",rotating_pipe:"断熱回転流路"};
const vortexDefaults={kind:"forced",rpm:30000,swirl_ratio:0.5,reference_radius_m:0.08,start_radius_m:0.08,end_radius_m:0.18,reference_pressure_pa:300000,static_temperature_k:600,points:21};
const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmt = (v, digits=5) => v == null ? "—" : Number(v).toLocaleString("ja-JP", {maximumSignificantDigits:digits});
const option = (value, label, active) => `<option value="${esc(value)}"${active === value ? " selected" : ""}>${esc(label)}</option>`;
function message(text, error=false) { $("message").hidden=!text; $("message").textContent=text; $("message").className=error?"error":""; }
async function api(path, data) {
  const response = await fetch(path, data === undefined ? {} : {method:"POST", headers:{"Content-Type":"application/json","X-Network-Token":token},body:JSON.stringify(data)});
  const value = await response.json();
  if (!response.ok) {
    const error = new Error(value.error || `HTTP ${response.status}`);
    error.diagnostics = value.diagnostics;
    throw error;
  }
  return value;
}
async function action(task) {
  const controls=[...document.querySelectorAll("main button,main input,main select,main textarea")].filter(e=>!e.disabled);
  document.body.classList.add("busy"); controls.forEach(e=>e.disabled=true);
  try { await task(); } catch(e) {
    if (e.diagnostics) renderConvergenceFailure(e.diagnostics);
    message(e.message,true);
  }
  finally { document.body.classList.remove("busy"); controls.filter(e=>e.isConnected).forEach(e=>e.disabled=false); resultButtons(); }
}
function resultButtons() { for(const id of ["save","export-result","export-csv"]) $(id).disabled=!result; $("export-grid").disabled=!gridResult; }
function invalidate() {
  hasEdits=true;
  result=null; sweepResult=null; vortexResult=null;
  gridResult=null; $("grid-content").replaceChildren(); $("grid-filter").hidden=true;
  $("vortex-content").replaceChildren();
  $("state").textContent="変更あり・未計算"; $("state").className="state";
  $("results-content").className="empty"; $("results-content").textContent="入力が変更されました。定常計算を実行してください。";
  $("sweep-content").replaceChildren(); resultButtons();
  if(model)renderDiagram();
}
function view(name) {
  currentView=name;
  document.querySelectorAll(".view").forEach(e=>e.hidden=e.id!==`view-${name}`);
  document.querySelectorAll("nav button").forEach(e=>e.classList.toggle("active",e.dataset.view===name));
  if(name==="cases") refreshCases().catch(e=>message(e.message,true));
  if(name==="sweep") renderSweepTargets();
  if(name==="vortex")renderVortexInputs();
}
function loadModel(m, r=null) {
  model=structuredClone(m); selected=model.edges[0]?.id;
  $("node-search").value=""; $("edge-search").value="";
  $("diagram-panel").open=!largeNetwork();
  invalidate();
  vortexResult=null; $("vortex-content").replaceChildren(); result=r; renderModel(); if(r) renderResults();
  hasEdits=false;message("");
}
function renderModel() {
  $("model-name").value=model.name; $("gas-r").value=model.gas.R; $("gas-gamma").value=model.gas.gamma;
  renderNodes(); renderEdges(); renderDetail(); renderDiagram(); renderSweepTargets(); resultButtons();
}
function nodeField(n,field,scale=1) {
  return `<input type="number" step="any" data-node="${esc(n.id)}" data-field="${field}" data-scale="${scale}" aria-label="${esc(n.name)} ${field}" value="${esc((n[field]??0)/scale)}">`;
}
function matchesSearch(query, ...fields) {
  return fields.some(value=>String(value ?? "").toLowerCase().includes(query));
}
function renderNodes() {
  const query=$("node-search").value.trim().toLowerCase();
  const nodes=model.nodes.filter(n=>matchesSearch(query,n.id,n.name));
  $("nodes-editor").innerHTML=nodes.map(n=>`<tr><td class="mono">${esc(n.id)}</td><td><input data-node="${esc(n.id)}" data-field="name" aria-label="${esc(n.id)} 名称" value="${esc(n.name)}"></td><td><select data-node="${esc(n.id)}" data-field="kind" aria-label="${esc(n.id)} 種類">${option("boundary","圧力・温度境界",n.kind)}${option("junction","内部混合室",n.kind)}</select></td><td><select data-node="${esc(n.id)}" data-field="frame" aria-label="${esc(n.id)} 座標系">${option("stationary","静止系",n.frame||"stationary")}${option("rotating","回転系・完全追従",n.frame)}</select></td><td>${nodeField(n,"rpm")}</td><td>${nodeField(n,"radius_m",.001)}</td><td>${nodeField(n,"pressure_pa",1000)}</td><td>${nodeField(n,"temperature_k")}</td><td>${nodeField(n,"heat_w")}</td><td><button class="remove" data-remove-node="${esc(n.id)}" aria-label="${esc(n.id)} を削除">×</button></td></tr>`).join("")||(query?'<tr><td colspan="10">該当するノードがありません。</td></tr>':"");
}
function renderEdges() {
  const query=$("edge-search").value.trim().toLowerCase();
  const names=new Map(model.nodes.map(n=>[n.id,n.name]));
  const edges=model.edges.filter(e=>matchesSearch(query,e.id,e.name,e.from,e.to,names.get(e.from),names.get(e.to)));
  $("edges-editor").innerHTML=edges.map(e=>`<tr data-select-edge="${esc(e.id)}" class="${selected===e.id?"selected":""}"><td><span class="name-cell">${esc(e.name)}</span><small class="mono">${esc(e.id)}</small></td><td>${esc(kindNames[e.kind])}</td><td class="mono">${esc(e.from)} → ${esc(e.to)}</td><td>${fmt(e.area_m2*1e6)}</td><td><button class="remove" data-remove-edge="${esc(e.id)}" aria-label="${esc(e.id)} を削除">×</button></td></tr>`).join("")||(query?'<tr><td colspan="5">該当する流路がありません。</td></tr>':"");
}
const defaults={area_m2:0.0001,cd:0.7,cd_reverse:0.7,diameter_m:0.02,length_m:1,roughness_m:0.00001,k:0,k_reverse:0,friction:"auto",friction_factor:0.02,cells:48,htc_w_m2k:0,wall_temperature_k:650,heat_w:0,source:"",notes:"",seal_diameter_m:0.12,clearance_m:0.00015,tooth_count:4,tooth_clearances_m:[]};
function completeEdge(e) {
  if(e.cd_reverse===undefined)e.cd_reverse=e.cd??0.7;
  if(e.k_reverse===undefined)e.k_reverse=e.k??0;
  for(const [key,value] of Object.entries(defaults)) if(e[key]===undefined) e[key]=value;
}
function edgeInput(e, field, label, scale=1) {
  return `<label>${label}<input type="number" step="${['cells','tooth_count'].includes(field)?'1':'any'}" data-edge="${esc(e.id)}" data-field="${field}" data-scale="${scale}" value="${esc(e[field]/scale)}"></label>`;
}
function renderDetail() {
  const original=model.edges.find(e=>e.id===selected);
  const e=original?structuredClone(original):null;
  $("edge-id").textContent=e?.id||"";
  if(!e) { $("edge-detail").innerHTML='<p class="padded help">流路を追加してください。</p>'; return; }
  completeEdge(e);
  const select=(field,label,opts)=>`<label>${label}<select data-edge="${esc(e.id)}" data-field="${field}">${opts.map(([v,l])=>option(v,l,e[field])).join("")}</select></label>`;
  let html=`<div class="fields"><label class="wide">名称<input data-edge="${esc(e.id)}" data-field="name" value="${esc(e.name)}"></label>`;
  html+=select("kind","種類",Object.entries(kindNames));
  if(e.kind!=="labyrinth")html+=edgeInput(e,"area_m2","面積 A [mm²]",1e-6);
  html+=select("from","正方向の始点",model.nodes.map(n=>[n.id,n.name]));
  html+=select("to","正方向の終点",model.nodes.map(n=>[n.id,n.name]));
  if(e.kind==="labyrinth") {
    html+=edgeInput(e,"seal_diameter_m","隙間の平均径 Dm [mm]",0.001)+edgeInput(e,"clearance_m","全歯共通の半径隙間 c [mm]",0.001)+edgeInput(e,"tooth_count","歯数（1～30）")+edgeInput(e,"cd","各歯の順方向 Cd [-]")+edgeInput(e,"cd_reverse","各歯の逆方向 Cd [-]");
    html+=`<label class="wide">歯ごとの隙間 [mm]（任意・カンマ区切り）<input data-edge="${esc(e.id)}" data-field="tooth_clearances_m" value="${esc(e.tooth_clearances_m.map(c=>fmtInput(c*1000)).join(', '))}"></label><div class="help wide">空欄なら全歯共通。記入時は歯数と同数で指定し、定義した正方向の順に並べます。A=πDm c。歯間の軸方向運動エネルギーは完全散逸。持越し・風損・ハニカムの補正は含みません。</div>`;
  } else if(e.kind==="orifice"||e.kind==="leak") {
    html+=edgeInput(e,"cd","順方向 Cd [-]")+edgeInput(e,"cd_reverse","逆方向 Cd [-]");
    html+='<div class="help wide">Cdは入力面積に対する実流量／理想流量。逆流時は逆方向Cdを使用します。</div>';
  } else {
    html+=edgeInput(e,"diameter_m","水力直径 Dh [mm]",0.001);
    if(["pipe","rotating_pipe"].includes(e.kind)) html+=edgeInput(e,"length_m","長さ L [m]");
    html+=edgeInput(e,"k","順方向 K [-]")+edgeInput(e,"k_reverse","逆方向 K [-]");
    if(["pipe","rotating_pipe"].includes(e.kind)) {
      html+=select("friction","摩擦の指定",[["auto","Re・粗さから計算"],["fixed","Darcy係数を指定"]]);
      html+=e.friction==="fixed" ? edgeInput(e,"friction_factor","Darcy摩擦係数 fD [-]") : edgeInput(e,"roughness_m","等価砂粒粗さ [mm]",0.001);
      if(e.kind==="pipe")html+=edgeInput(e,"htc_w_m2k","熱伝達率 h [W/(m²·K)]")+edgeInput(e,"wall_temperature_k","壁温 Tw [K]")+edgeInput(e,"heat_w","指定熱入力 [W]");
    }
    html+=edgeInput(e,"cells","分割数（8～256）");
    html+='<div class="help wide">入口は等エントロピー加速、出口は下流混合室の静圧に整合。Kは等価分布抵抗として扱います。</div>';
    if(e.kind==="rotating_pipe")html+='<div class="help wide">両端ノードの半径・回転数を使用。断熱・完全追従・同一回転数。ロタルピーと回転仕事を計算します。静止系との直接接続はできません。</div>';
  }
  html+=`<label class="wide">係数・形状の出典 / 適用範囲<textarea data-edge="${esc(e.id)}" data-field="source">${esc(e.source)}</textarea></label><label class="wide">設計メモ<textarea data-edge="${esc(e.id)}" data-field="notes">${esc(e.notes)}</textarea></label></div>`;
  $("edge-detail").innerHTML=html;
}
function fmtInput(v){return Number(Number(v).toPrecision(10));}
function largeNetwork() {return model.nodes.length>25||model.edges.length>60;}
function renderDiagram() {
  $("network-count").textContent=`ノード ${model.nodes.length} / ${maxNodes} · 流路 ${model.edges.length} / ${maxEdges}`;
  if (!$("diagram-panel").open) {
    $("diagram").replaceChildren();
    return;
  }
  const ranks=new Map(), pending=new Set(model.nodes.map(n=>n.id));
  const incoming=new Map(model.nodes.map(n=>[n.id,[]]));
  for(const e of model.edges) incoming.get(e.to)?.push(e.from);
  for(let pass=0;pass<model.nodes.length;pass++) {
    let changed=false;
    for(const id of [...pending]) {
      const parents=incoming.get(id);
      if(parents.every(p=>ranks.has(p))) { ranks.set(id,parents.length?Math.max(...parents.map(p=>ranks.get(p)))+1:0); pending.delete(id);changed=true; }
    }
    if(!changed) break;
  }
  // Cycles have no topological order; lay remaining nodes in a separate column sequence.
  for(const id of pending) ranks.set(id,Math.min(ranks.size,4));
  const maxRank=Math.max(1,...ranks.values()), width=Math.max(850,215*(maxRank+1));
  const groups=Array.from({length:maxRank+1},()=>[]);
  model.nodes.forEach(n=>groups[ranks.get(n.id)].push(n));
  const height=Math.max(300,Math.max(...groups.map(g=>g.length))*110+50), positions=new Map();
  groups.forEach((group,col)=>group.forEach((n,row)=>positions.set(n.id,{x:35+col*(width-220)/maxRank,y:(row+1)*height/(group.length+1)-29})));
  const edgeResults=new Map(result?.edges.map(e=>[e.id,e]));
  const nodeResults=new Map(result?.nodes.map(n=>[n.id,n]));
  let svg=`<svg class="${largeNetwork()?"diagram-large":""}" viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" role="img" aria-label="${esc(model.name)}の接続図"><defs><marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#7a9585"/></marker></defs>`;
  for(const e of model.edges) {
    const a=positions.get(e.from),b=positions.get(e.to); if(!a||!b) continue;
    const r=edgeResults.get(e.id), forward=b.x>a.x;
    const x1=a.x+(forward?155:0),x2=b.x+(forward?0:155),y1=a.y+29,y2=b.y+29;
    const bend=Math.max(35,Math.abs(x2-x1)/2);
    const path=`M${x1},${y1} C${x1+(forward?bend:-bend)},${y1} ${x2+(forward?-bend:bend)},${y2} ${x2},${y2}`;
    svg+=`<path d="${path}" class="svg-edge ${r?.choked?'choked':''}" marker-${r?.reversed?'start':'end'}="url(#arrow)"><title>${esc(e.name)} / ${esc(e.id)}</title></path>`;
    const label=r?`${e.id} · ${fmt(Math.abs(r.mass_flow_kg_s),4)} kg/s` : e.id;
    svg+=`<rect x="${(x1+x2)/2-62}" y="${(y1+y2)/2-21}" width="124" height="16" rx="3" fill="white"/><text x="${(x1+x2)/2}" y="${(y1+y2)/2-9}" text-anchor="middle" class="svg-edge-label">${esc(label)}</text>`;
  }
  for(const n of model.nodes) {
    const pos=positions.get(n.id),r=nodeResults.get(n.id),b=n.kind==="boundary"?"boundary":"";
    const label=n.name.length>14?n.name.slice(0,13)+"…":n.name;
    const detail=r?`${fmt(r.pressure_pa/1000,4)} kPa · ${fmt(r.temperature_k,4)} K`:`${n.id} / ${b?'境界':'内部混合室'}`;
    svg+=`<g><title>${esc(n.name)}</title><rect x="${pos.x}" y="${pos.y}" width="155" height="58" rx="7" class="svg-box ${b}"/><text x="${pos.x+12}" y="${pos.y+23}" class="svg-node-label ${b}">${esc(label)}</text><text x="${pos.x+12}" y="${pos.y+43}" class="svg-state ${b}">${esc(detail)}</text></g>`;
  }
  $("diagram").innerHTML=svg+"</svg>";
}
function table(headers, rows) {return `<div class="table-scroll"><table><thead><tr>${headers.map(h=>`<th>${h}</th>`).join("")}</tr></thead><tbody>${rows.map(cells=>`<tr>${cells.map(c=>`<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;}

function renderConvergenceFailure({phase, nodes}) {
  const phases = {
    cold_pressure: ["初期圧力計算の質量収支", "kg/s"],
    pressure: ["圧力反復の質量収支", "kg/s"],
    thermal: ["温度反復のエネルギー収支", "W"],
  };
  const [label, unit] = phases[phase];
  const rows = nodes.map(node => [
    `${esc(node.node_name)}<br><small>${esc(node.node_id)}</small>`,
    node.residual.toExponential(4),
    node.tolerance.toExponential(4),
    (Math.abs(node.residual) - node.tolerance).toExponential(4),
  ]);
  const columns = ["ノード名 / ID", `残差 [${unit}]`, `許容値 [${unit}]`, `超過量 [${unit}]`];
  result = null;
  $("state").textContent = "未収束";
  $("state").className = "state";
  $("results-content").className = "";
  $("results-content").innerHTML = `<div class="panel"><div class="panel-head"><h3>収束診断 <span>${label}</span></h3></div>${table(columns, rows)}</div>`;
  resultButtons();
  renderDiagram();
  view("results");
}
function absolutePressureKpa(n){const p=n.absolute_total_pressure_pa===undefined?n.pressure_pa:n.absolute_total_pressure_pa;return p==null?null:p/1000;}
function renderResults() {
  if(!result) return;
  const s=result.summary;
  $("state").textContent="収束済み"; $("state").className="state ok";
  const metrics=[["総供給流量",fmt(s.supply_kg_s,6),"kg/s","外部境界からの正味供給の合計"],["熱入力の合計",fmt(s.heat_w,6),"W","流路の伝熱と混合室の熱入力"],["チョークした流路",s.choked_count,"本",`${result.edges.length}流路中 / 逆流 ${result.edges.filter(r=>r.reversed).length}本`],["最大質量残差",Number(s.max_mass_residual_kg_s).toExponential(2),"kg/s",`最大熱残差 ${fmt(s.max_energy_residual_w,3)} W`]];
  let html=`<div class="metrics">${metrics.map(([label,value,unit,note])=>`<div class="metric"><label>${label}</label><strong>${value}<small>${unit}</small></strong><p>${esc(note)}</p></div>`).join("")}</div>`;
  html+='<div class="panel"><div class="panel-head"><h3>ノード状態 <span>絶対圧力 / 混合全温</span></h3></div>';
  html+=table(["名称 / ID・座標系","静圧 [kPa abs]","各系全温 [K]","絶対全温 [K]","絶対全圧 [kPa]","流入 [kg/s]","流出 [kg/s]","質量残差 [kg/s]","エネルギー残差 [W]"],result.nodes.map(n=>[`${esc(n.name)}<br><small>${esc(n.id)} · ${n.kind==='boundary'?'境界':'混合室'} · ${n.frame==='rotating'?`${fmt(n.rpm)} rpm / ${fmt(n.radius_m*1000)} mm`:'静止系'}</small>`,fmt(n.pressure_pa/1000,7),fmt(n.temperature_k,7),fmt(n.absolute_total_temperature_k??n.temperature_k,7),fmt(absolutePressureKpa(n),7),fmt(n.inflow_kg_s,6),fmt(n.outflow_kg_s,6),n.mass_residual_kg_s==null?"境界":n.mass_residual_kg_s.toExponential(2),n.energy_residual_w==null?"境界":n.energy_residual_w.toExponential(2)]))+"</div>";
  html+='<div class="panel"><div class="panel-head"><h3>流路状態 <span>流量の符号は定義した正方向に対する値</span></h3></div>';
  html+=table(["名称 / ID","流量 [kg/s]","実際の流向","出口全温 [K]","熱入力 [W]","最大M","Re / fD","状態"],result.edges.map(e=>[`${esc(e.name)}<br><small>${esc(e.id)}</small>`,fmt(e.mass_flow_kg_s,7),`${esc(e.upstream)} → ${esc(e.downstream)}`,fmt(e.outlet_total_temperature_k,7),fmt(e.heat_w,6),fmt(e.max_mach,5),e.reynolds==null?"—":`${fmt(e.reynolds,4)} / ${fmt(e.friction_factor,4)}`,`${e.choked?'<span class="badge">CHOKED</span>':'<span class="badge">亜音速 / 停滞</span>'} ${e.reversed?'<span class="badge reverse">逆流</span>':''}`]))+"</div>";
  for(const seal of result.edges.filter(e=>e.teeth)) {
    html+=`<div class="panel"><div class="panel-head"><h3>${esc(seal.name)} · 歯間圧力<span>実際の流向順 / 歯番号は定義した正方向の番号</span></h3></div>${table(["歯番号","隙間 [mm]","上流混合圧 [kPa]","下流混合圧 [kPa]","歯先静圧 [kPa]","流量 [kg/s]","M / 状態"],seal.teeth.map(t=>[t.tooth,fmt(t.clearance_m*1000,6),fmt(t.upstream_pressure_pa/1000,7),fmt(t.downstream_pressure_pa/1000,7),fmt(t.throat_static_pressure_pa/1000,7),fmt(t.mass_flow_kg_s,7),`${fmt(t.mach,5)} ${t.choked?'<span class="badge">CHOKED</span>':''}`]))}<p class="padded help">各歯の最大流量不一致 ${seal.max_tooth_mass_residual_kg_s.toExponential(2)} kg/s。完全散逸モデルの結果です。</p></div>`;
  }
  if(result.edges.some(e=>e.kind==="rotating_pipe"))html+=`<div class="panel"><div class="panel-head"><h3>回転流路のエネルギー<span>正の軸動力は流体への入力</span></h3></div>${table(["流路","入口相対全温 [K]","出口相対全温 [K]","入口絶対全温 [K]","出口絶対全温 [K]","軸動力 [W]","トルク [N·m]","ロタルピー残差 [J/kg]"],result.edges.filter(e=>e.kind==="rotating_pipe").map(e=>[esc(e.name),fmt(e.inlet_total_temperature_k,7),fmt(e.outlet_total_temperature_k,7),fmt(e.inlet_absolute_total_temperature_k,7),fmt(e.outlet_absolute_total_temperature_k,7),fmt(e.shaft_work_w,7),fmt(e.torque_nm,6),e.rothalpy_residual_j_kg.toExponential(2)]))}</div>`;
  html+=`<div class="panel padded"><h3>収束と全体収支</h3><p class="help">圧力反復 ${result.iterations.pressure_iterations}回 / 熱連成 ${result.iterations.thermal_iterations}回。絶対全エンタルピーと熱入力・軸動力を含めて保存則を確認します。</p>${table(["全体質量残差 [kg/s]","全体エネルギー残差 [W]","合計軸動力 [W]","枝の最大圧力残差 [Pa]"],[[s.global_mass_residual_kg_s.toExponential(3),s.global_energy_residual_w.toExponential(3),fmt(s.shaft_work_w||0,7),fmt(s.max_branch_pressure_residual_pa,4)]])}<p class="help">チョーク時は出口圧力と背圧が一致しないため、圧力残差の対象外です。</p></div>`;
  html+=`<details class="warnings" open><summary>モデルの適用範囲・注記（${result.warnings.length}）</summary><ul>${result.warnings.map(w=>`<li>${esc(w)}</li>`).join("")}</ul></details>`;
  $("results-content").className=""; $("results-content").innerHTML=html; resultButtons(); renderDiagram();
}
function nextId(prefix,items) {let n=1;while(items.some(e=>e.id===prefix+n))n++;return prefix+n;}
function download(name,content,type) {const url=URL.createObjectURL(new Blob([content],{type}));const a=document.createElement("a");a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
function exportCSV() {
  if(!result) return;
  const rows=[["section","id","name","pressure_kpa_abs","total_temperature_k","mass_flow_kg_s","from","to","choked","reversed","heat_w","mass_residual_kg_s","energy_residual_w","frame","rpm","radius_m","absolute_total_temperature_k","absolute_total_pressure_pa","shaft_work_w"]];
  result.nodes.forEach(n=>rows.push(["node",n.id,n.name,n.pressure_pa/1000,n.temperature_k,"","","","","","",n.mass_residual_kg_s,n.energy_residual_w,n.frame||"stationary",n.rpm||0,n.radius_m||0,n.absolute_total_temperature_k??n.temperature_k,n.absolute_total_pressure_pa===undefined?n.pressure_pa:n.absolute_total_pressure_pa,""]));
  result.edges.forEach(e=>rows.push(["edge",e.id,e.name,"",e.outlet_total_temperature_k,e.mass_flow_kg_s,e.upstream,e.downstream,e.choked,e.reversed,e.heat_w,"","",e.reference_frame||"stationary",e.rpm||0,e.outlet_radius_m||0,e.outlet_absolute_total_temperature_k??e.outlet_total_temperature_k,"",e.shaft_work_w||0]));
  // Spreadsheet formula injection: prefix user-provided text starting with formula characters.
  const cell=v=>{let s=String(v??"");if(typeof v==="string"&&/^[=+\-@\t\r]/.test(s))s="'"+s;return '"'+s.replace(/"/g,'""')+'"';};
  download("secondary-air-results.csv","\ufeff"+rows.map(r=>r.map(cell).join(",")).join("\r\n"),"text/csv;charset=utf-8");
}
const sweepFields={pressure_pa:"境界圧力 [Pa abs]",temperature_k:"境界全温（各系）[K]",heat_w:"熱入力 [W]",area_m2:"面積 [m²]",cd:"順方向 Cd [-]",cd_reverse:"逆方向 Cd [-]",length_m:"長さ [m]",k:"順方向 K [-]",k_reverse:"逆方向 K [-]",wall_temperature_k:"壁温 [K]",htc_w_m2k:"熱伝達率 [W/(m²·K)]",friction_factor:"Darcy係数 [-]",clearance_m:"半径隙間 [m]",seal_diameter_m:"隙間平均径 [m]",tooth_count:"歯数（整数点のみ）",rpm:"接続した回転系の回転数 [rpm]"};
function rotorGroups(){
  const found=new Set(),groups=[];
  for(const n of model.nodes){
    if(n.frame!=="rotating"||found.has(n.id))continue;
    const queue=[n.id];
    while(queue.length){const key=queue.pop();if(found.has(key))continue;found.add(key);for(const e of model.edges){if(e.from===key)queue.push(e.to);if(e.to===key)queue.push(e.from);}}
    groups.push(n);
  }
  return groups;
}
function renderSweepTargets() {
  const previous=$("sweep-target").value;
  $("sweep-target").innerHTML=model.nodes.map(n=>option(`nodes:${n.id}`,`ノード / ${n.name}`,previous)).join("")+model.edges.map(e=>option(`edges:${e.id}`,`流路 / ${e.name}`,previous)).join("")+rotorGroups().map(n=>option(`rotation:${n.id}`,`回転系 / ${n.name}`,previous)).join("");
  renderSweepFields(false);
}
function sweepTarget() {const v=$("sweep-target").value,sep=v.indexOf(":");return {group:v.slice(0,sep),id:v.slice(sep+1),field:$("sweep-field").value};}
function renderSweepFields(reset=true) {
  const target=sweepTarget(),obj=model[target.group==="rotation"?"nodes":target.group]?.find(o=>o.id===target.id);if(!obj)return;
  const previous=$("sweep-field").value;
  let fields;
  if(target.group==="rotation")fields=["rpm"];
  else if(target.group==="nodes") fields=obj.kind==="boundary"?["pressure_pa","temperature_k"]:["heat_w"];
  else if(["orifice","leak"].includes(obj.kind)) fields=["area_m2","cd","cd_reverse"];
  else if(obj.kind==="labyrinth")fields=[...((obj.tooth_clearances_m||[]).length?[]:["clearance_m"]),"seal_diameter_m","cd","cd_reverse",...((obj.tooth_clearances_m||[]).length?[]:["tooth_count"])];
  else fields=obj.kind==="resistance"?["area_m2","k","k_reverse"]:["area_m2","length_m","k","k_reverse",...(obj.kind==="rotating_pipe"?[]:["wall_temperature_k","htc_w_m2k","heat_w"]),...(obj.friction==="fixed"?["friction_factor"]:[])];
  $("sweep-field").innerHTML=fields.map(f=>option(f,sweepFields[f],previous)).join("");
  if(reset) resetSweepRange();
}
function resetSweepRange() {const t=sweepTarget(),v=model[t.group==="rotation"?"nodes":t.group]?.find(o=>o.id===t.id)?.[t.field]??0;$("sweep-start").value=t.field==="tooth_count"?Math.max(1,v-2):Number((v*.8).toPrecision(8));$("sweep-end").value=t.field==="tooth_count"?Math.min(30,v+2):Number((v? v*1.2:1).toPrecision(8));if(t.field==="tooth_count")$("sweep-count").value=Number($("sweep-end").value)-Number($("sweep-start").value)+1;}
function renderSweep() {
  const hasEdge=sweepResult.target.group==="edges";
  const rows=sweepResult.points.map(p=>[fmt(p.value,7),p.status==='converged'?'収束':'未成立',p.summary?fmt(p.summary.supply_kg_s,7):'—',...(hasEdge?[fmt(p.edges?.find(e=>e.id===sweepResult.target.id)?.mass_flow_kg_s,7)]:[]),p.summary?fmt(p.summary.heat_w,6):'—',p.summary?fmt(p.summary.shaft_work_w||0,7):'—',p.summary?p.summary.choked_count:esc(p.error)]);
  const valid=sweepResult.points.filter(p=>p.status==='converged');
  let svg="";
  if(valid.length>1) {
    const xs=valid.map(p=>p.value),ys=valid.map(p=>p.summary.supply_kg_s),xmin=Math.min(...xs),xspan=Math.max(...xs)-xmin||1,ymin=Math.min(...ys),yspan=Math.max(...ys)-ymin||Math.max(Math.abs(ymin)*.1,1e-6);
    const coords=valid.map(p=>({x:75+(p.value-xmin)/xspan*800,y:165-(p.summary.supply_kg_s-ymin)/yspan*125,p}));
    // Keep failed points as visible gaps, rather than drawing across them.
    let path="",prior=-2;
    for(const c of coords){const index=sweepResult.points.indexOf(c.p);path+=`${index===prior+1?'L':'M'}${c.x},${c.y} `;prior=index;}
    svg=`<svg viewBox="0 0 950 210" role="img" aria-label="感度解析の総供給流量"><path d="M75 20 V170 H900" stroke="#ccd8ca" fill="none"/><path d="${path}" class="line-chart"/>${coords.map(c=>`<circle cx="${c.x}" cy="${c.y}" r="4" class="sweep-point"><title>${fmt(c.p.value)}: ${fmt(c.p.summary.supply_kg_s)} kg/s</title></circle>`).join("")}<text x="75" y="196" class="svg-state">${fmt(xmin,5)}</text><text x="820" y="196" class="svg-state">${fmt(Math.max(...xs),5)}</text><text x="80" y="15" class="svg-state">総供給流量 [kg/s] · ${fmt(ymin,5)} ～ ${fmt(ymin+yspan,5)}</text></svg>`;
  }
  $("sweep-content").innerHTML=`<div class="panel">${svg}${table([esc(sweepFields[sweepResult.target.field]),"状態","総供給流量 [kg/s]",...(hasEdge?["対象流路の流量 [kg/s]"]:[]),"総熱入力 [W]","軸動力 [W]","チョーク数 / 未成立の理由"],rows)}<button id="export-sweep">感度解析JSONを書出し</button></div>`;
}
async function refreshCases() {
  const checked=[...document.querySelectorAll('input[name="compare"]:checked')].map(e=>e.value);
  cases=await api("/api/cases");
  $("cases-list").innerHTML=cases.length?cases.map(c=>`<div class="case-row"><input type="checkbox" name="compare" value="${esc(c.id)}" aria-label="${esc(c.name)} を比較"${checked.includes(c.id)?" checked":""}><div class="case-meta"><strong>${esc(c.name)}</strong><small>${esc(new Date(c.created).toLocaleString("ja-JP"))}</small></div><button data-load="${esc(c.id)}">読込</button><button data-case-export="${esc(c.id)}">JSON</button></div>`).join("")+'<div class="case-actions"><button id="compare-cases" class="primary">選択した2ケースを比較</button></div>':'<div class="padded help">保存ケースはまだありません。定常計算後に「ケース保存」を押してください。</div>';
}
async function compareCases() {
  const ids=[...document.querySelectorAll('input[name="compare"]:checked')].map(e=>e.value);
  if(ids.length!==2)throw new Error("比較するケースを2つ選択してください。");
  const [a,b]=await Promise.all(ids.map(id=>api(`/api/case?id=${encodeURIComponent(id)}`)));
  const row=(label,x,y,unit="")=>[esc(label),typeof x==="number"?fmt(x,8):esc(x??"—"),typeof y==="number"?fmt(y,8):esc(y??"—"),typeof x==="number"&&typeof y==="number"?`<span class="comparison-highlight">${fmt(y-x,6)} ${unit}</span>`:x===y?"—":"変更"];
  const rows=[row("総供給流量 [kg/s]",a.result.summary.supply_kg_s,b.result.summary.supply_kg_s),row("総熱入力 [W]",a.result.summary.heat_w,b.result.summary.heat_w),row("R [J/(kg·K)]",a.result.gas.R,b.result.gas.R),row("γ [-]",a.result.gas.gamma,b.result.gas.gamma)];
  rows.push(row("合計軸動力 [W]",a.result.summary.shaft_work_w||0,b.result.summary.shaft_work_w||0));
  for(const group of ["nodes","edges"]) {
    const keys=new Set([...a.result[group],...b.result[group]].map(o=>o.id));
    for(const key of keys) {
      const x=a.result[group].find(o=>o.id===key),y=b.result[group].find(o=>o.id===key);
      if(!x||!y){rows.push(row(`${group} / ${key}`,x?"あり":"なし",y?"あり":"なし"));continue;}
      if(group==="nodes"){rows.push(row(`${key} 圧力 [kPa]`,x.pressure_pa/1000,y.pressure_pa/1000));rows.push(row(`${key} 各系全温 [K]`,x.temperature_k,y.temperature_k));rows.push(row(`${key} 絶対全温 [K]`,x.absolute_total_temperature_k??x.temperature_k,y.absolute_total_temperature_k??y.temperature_k));}
      else{rows.push(row(`${key} 流量 [kg/s]`,x.mass_flow_kg_s,y.mass_flow_kg_s));rows.push(row(`${key} 出口全温 [K]`,x.outlet_total_temperature_k,y.outlet_total_temperature_k));}
      const xm=a.result.model[group].find(o=>o.id===key),ym=b.result.model[group].find(o=>o.id===key);
      const relevant=o=>group==="nodes"?Object.keys(o):["name","kind","from","to","area_m2","source","notes","heat_w",...(o.kind==="labyrinth"?["cd","cd_reverse","seal_diameter_m","clearance_m","tooth_count","tooth_clearances_m"]:["orifice","leak"].includes(o.kind)?["cd","cd_reverse"]:["diameter_m","length_m","k","k_reverse","friction",o.friction==="fixed"?"friction_factor":"roughness_m","cells","htc_w_m2k","wall_temperature_k"])];
      for(const field of new Set([...relevant(xm),...relevant(ym)]))if(field!=="id"&&JSON.stringify(xm[field])!==JSON.stringify(ym[field]))rows.push(row(`${key} 入力 ${field}（SI）`,Array.isArray(xm[field])?JSON.stringify(xm[field]):xm[field],Array.isArray(ym[field])?JSON.stringify(ym[field]):ym[field]));
    }
  }
  $("comparison").innerHTML=`<div class="panel"><div class="panel-head"><h3>ケース比較 <span>IDを対応付けて比較。差はB−A。</span></h3></div>${table(["項目",`A / ${esc(a.name)}`,`B / ${esc(b.name)}`,"差 / 変更"],rows)}</div>`;
}
document.querySelector("nav").addEventListener("click",e=>{const b=e.target.closest("[data-view]");if(b)view(b.dataset.view);});
document.addEventListener("input",e=>{
  const el=e.target;
  if(el.id==="model-name"){model.name=el.value;invalidate();}
  else if(el.id==="gas-r"||el.id==="gas-gamma"){model.gas[el.id==="gas-r"?"R":"gamma"]=el.value===""?null:Number(el.value);invalidate();}
  else if(el.dataset.node||el.dataset.edge) {
    const group=el.dataset.node?model.nodes:model.edges,key=el.dataset.node||el.dataset.edge,obj=group.find(o=>o.id===key);
    if(el.dataset.field==="tooth_clearances_m")obj[el.dataset.field]=el.value.trim()?el.value.split(/[,、;\s]+/).filter(Boolean).map(x=>Number(x)*.001):[];
    else obj[el.dataset.field]=el.type==="number"?(el.value===""?null:Number(el.value)*Number(el.dataset.scale||1)):el.value;
    if(obj.kind==="labyrinth")obj.area_m2=Math.PI*(obj.seal_diameter_m??.12)*(obj.clearance_m??.00015);
    invalidate();
  }
  else if(el.dataset.vortex){
    model.vortex_study??={...vortexDefaults};
    model.vortex_study[el.dataset.vortex]=el.type==="number"?(el.value===""?null:Number(el.value)*Number(el.dataset.scale||1)):el.value;
    vortexResult=null;$("vortex-content").replaceChildren();invalidate();
  }
});
document.addEventListener("change",e=>{
  const el=e.target;
  if(el.dataset.edge) {
    const obj=model.edges.find(o=>o.id===el.dataset.edge);
    if(el.dataset.field==="kind") {
      completeEdge(obj);
      if(["orifice","leak","labyrinth"].includes(obj.kind))obj.heat_w=0;
      if(obj.kind==="labyrinth")obj.area_m2=Math.PI*obj.seal_diameter_m*obj.clearance_m;
      if(obj.kind==="resistance"){obj.length_m=0;obj.htc_w_m2k=0;obj.heat_w=0;obj.friction="fixed";obj.friction_factor=0;}
      if(obj.kind==="rotating_pipe"){obj.heat_w=0;obj.htc_w_m2k=0;}
      if(["pipe","rotating_pipe"].includes(obj.kind)&&obj.length_m===0)obj.length_m=1;
    }
    renderEdges(); if(["kind","friction"].includes(el.dataset.field))renderDetail();renderDiagram();renderSweepTargets();
  }
  if(el.dataset.node){const n=model.nodes.find(n=>n.id===el.dataset.node);if(el.dataset.field==="kind"){if(n.kind==="boundary")n.heat_w=0;renderNodes();}if(el.dataset.field==="frame"){if(n.frame==="stationary")n.rpm=0;else {n.radius_m||=.1;n.rpm||=30000;}renderNodes();message("座標系の圧力・温度は自動換算しません。全接続の座標系・回転数と入力値を確認してください。");}renderDiagram();renderDetail();renderSweepTargets();}
});
document.addEventListener("click",e=>{
  const b=e.target.closest("button"),row=e.target.closest("[data-select-edge]");
  if(b?.dataset.removeNode){const id=b.dataset.removeNode;if(model.edges.some(o=>o.from===id||o.to===id)){message("このノードに接続する流路を先に削除してください。",true);return;}model.nodes=model.nodes.filter(o=>o.id!==id);invalidate();renderModel();}
  else if(b?.dataset.removeEdge){model.edges=model.edges.filter(o=>o.id!==b.dataset.removeEdge);selected=model.edges[0]?.id;invalidate();renderModel();}
  else if(row){selected=row.dataset.selectEdge;renderEdges();renderDetail();}
  else if(b?.dataset.load)action(async()=>{const c=await api(`/api/case?id=${encodeURIComponent(b.dataset.load)}`);if(!canReplace())return;loadModel(c.result.model,c.result);view("model");message(`「${c.name}」を読み込みました。保存時の計算結果です。`);});
  else if(b?.dataset.caseExport)action(async()=>{const c=await api(`/api/case?id=${encodeURIComponent(b.dataset.caseExport)}`);download("secondary-air-case.json",JSON.stringify(c.result,null,2),"application/json");});
  else if(b?.id==="compare-cases")action(compareCases);
  else if(b?.id==="export-sweep"&&sweepResult)download("secondary-air-sweep.json",JSON.stringify(sweepResult,null,2),"application/json");
});
function canReplace(){return !model||!hasEdits||confirm("現在の編集内容を置き換えます。必要な入力JSONやケースは保存しましたか？");}
$("sample").onclick=()=>{if(canReplace())action(async()=>{loadModel(await api("/api/sample"));view("model");message("架空サンプルを読み込みました。実機データではありません。");});};
$("sample-stage2").onclick=()=>{if(canReplace())action(async()=>{loadModel(await api("/api/sample-stage2"));view("model");message("4歯ラビリンスの架空サンプルです。渦の設定も含みますが、ネットワークには連成しません。");});};
$("sample-rotating").onclick=()=>{if(canReplace())action(async()=>{loadModel(await api("/api/sample-rotating"));view("model");message("同一回転系の断熱流路サンプルです。境界の温度は相対全温。回転仕事を含む絶対エネルギー収支を計算します。");});};
$("new").onclick=()=>{if(!canReplace())return;loadModel({schema_version:1,name:"新しいネットワーク",gas:{R:287.05,gamma:1.4},nodes:[{id:"inlet",name:"供給境界",kind:"boundary",pressure_pa:500000,temperature_k:550,heat_w:0},{id:"outlet",name:"排出境界",kind:"boundary",pressure_pa:100000,temperature_k:700,heat_w:0}],edges:[{id:"e1",name:"計量孔",kind:"orifice",from:"inlet",to:"outlet",...defaults}]});view("model");};
$("node-search").oninput=()=>{if(model)renderNodes();};
$("edge-search").oninput=()=>{if(model)renderEdges();};
$("diagram-panel").ontoggle=()=>{if(model)renderDiagram();};
$("add-node").onclick=()=>{if(model.nodes.length>=maxNodes){message(`ノードは${maxNodes}個までです。`,true);return;}const id=nextId("n",model.nodes);model.nodes.push({id,name:"新しい混合室",kind:"junction",pressure_pa:300000,temperature_k:550,heat_w:0});$("node-search").value="";invalidate();renderModel();};
$("add-edge").onclick=()=>{if(model.edges.length>=maxEdges){message(`流路は${maxEdges}本までです。`,true);return;}const id=nextId("e",model.edges);model.edges.push({id,name:"新しい流路",kind:"orifice",from:model.nodes[0]?.id,to:model.nodes[1]?.id,...defaults});selected=id;$("edge-search").value="";invalidate();renderModel();};
$("calculate").onclick=()=>action(async()=>{message("定常ネットワークを計算しています…");result=await api("/api/solve",{model});model=result.model;renderResults();view("results");message("圧力・流量・温度が収束しました。残差とモデルの注記を確認してください。");});
$("save").onclick=()=>action(async()=>{if(!result)return;const c=await api("/api/save",{model});result=c.result;hasEdits=false;renderResults();message(`「${c.name}」を新しいケースとして保存しました。`);});
$("import").onclick=()=>$("file").click();
$("file").onchange=()=>action(async()=>{const f=$("file").files[0];if(!f)return;try{if(f.size>maxImportMiB*1024*1024)throw new Error(`JSONファイルは${maxImportMiB} MiB以内です。`);const raw=JSON.parse((await f.text()).replace(/^\uFEFF/,""));const validated=await api("/api/validate",{model:raw.model||raw.result?.model||raw});if(canReplace()){loadModel(validated);view("model");message("入力JSONを読み込みました。計算結果は再計算してください。");}}finally{$("file").value="";}});
$("export-model").onclick=()=>action(async()=>{const validated=await api("/api/validate",{model});download("secondary-air-input.json",JSON.stringify(validated,null,2),"application/json");message("入力JSONを書き出しました。");});
$("export-result").onclick=()=>{if(result)download("secondary-air-results.json",JSON.stringify(result,null,2),"application/json");};
$("export-csv").onclick=exportCSV;
$("refresh-cases").onclick=()=>action(refreshCases);
$("sweep-target").onchange=()=>renderSweepFields();
$("sweep-field").onchange=resetSweepRange;
$("run-sweep").onclick=()=>action(async()=>{const count=Number($("sweep-count").value),start=Number($("sweep-start").value),end=Number($("sweep-end").value);if(!Number.isInteger(count)||count<2||count>15||!Number.isFinite(start)||!Number.isFinite(end)||start===end)throw new Error("異なる開始・終了値と2～15の整数点数を指定してください。");message("各運転点を計算しています…");sweepResult=await api("/api/sweep",{model,target:sweepTarget(),values:Array.from({length:count},(_,i)=>start+(end-start)*i/(count-1))});renderSweep();message("感度解析が完了しました。各点の状態を確認してください。");});
window.addEventListener("beforeunload",e=>{if(model&&hasEdits){e.preventDefault();e.returnValue="";}});
api("/api/sample").then(m=>{loadModel(m);$("state").textContent="未計算";message("架空サンプルを表示しています。境界・係数を確認して計算を実行してください。");}).catch(e=>message(e.message,true));

function renderVortexInputs(){
  const v=model.vortex_study||vortexDefaults;
  for(const el of document.querySelectorAll("[data-vortex]"))el.value=el.type==="number"?v[el.dataset.vortex]/Number(el.dataset.scale||1):v[el.dataset.vortex];
}
function renderVortex(){
  const p=vortexResult.profile,dp=vortexResult.pressure_difference_pa,ratio=vortexResult.pressure_ratio_end_start;
  let html=`<div class="metrics"><div class="metric"><label>開始点 → 終了点の静圧比</label><strong>${fmt(ratio,7)}</strong><p>指定スワールの半径方向平衡</p></div><div class="metric"><label>静圧差</label><strong>${fmt(dp/1000,7)}<small>kPa</small></strong><p>静温を固定した圧力分布</p></div></div>`;
  html+=`<div class="panel"><div class="panel-head"><h3>半径方向プロファイル<span>絶対系の接線速度・静圧・静温</span></h3></div>${table(["半径 [mm]","静圧 [kPa abs]","静温 [K]","全温 [K]","接線速度 [m/s]","接線M","密度 [kg/m³]"],p.map(x=>[fmt(x.radius_m*1000,6),fmt(x.static_pressure_pa/1000,7),fmt(x.static_temperature_k,6),fmt(x.total_temperature_k,7),fmt(x.tangential_velocity_m_s,6),fmt(x.tangential_mach,5),fmt(x.density_kg_m3,6)]))}</div><div class="warnings"><ul>${vortexResult.warnings.map(x=>`<li>${esc(x)}</li>`).join("")}</ul></div>`;
  html+='<p><button id="export-vortex">渦計算JSONを書出し</button></p>';
  $("vortex-content").innerHTML=html;
  $("export-vortex").onclick=()=>download("secondary-air-vortex.json",JSON.stringify(vortexResult,null,2),"application/json");
}
$("run-vortex").onclick=()=>action(async()=>{
  vortexResult=await api("/api/vortex",{vortex:model.vortex_study||vortexDefaults,gas:model.gas});
  renderVortex();message("等温の渦圧力分布を計算しました。ネットワークの流量・熱収支とは独立した補助計算です。");
});

const gridLabels={mass_flow_kg_s:"流路流量",pressure_pa:"ノード静圧",temperature_k:"ノード各系全温",absolute_total_temperature_k:"ノード絶対全温",outlet_total_temperature_k:"流路出口各系全温",heat_w:"流路熱入力",shaft_work_w:"流路軸動力",max_mach:"流路最大マッハ数"};
const gridStatus={within_tolerance:"比較許容値内",review_required:"要確認",incomplete:"一部未成立",unavailable:"比較対象外 / 不定"};
const gridTrend={small_change:"微小変化",decreasing:"変化量減少",growing:"変化量増大",oscillating:"振動",unavailable:"—"};
function renderGrid(){
  const g=gridResult;if(!g)return;
  let html=`<div class="panel padded"><h3>${esc(gridStatus[g.assessment])}</h3><p>比較相対許容値 ${fmt(g.relative_tolerance_percent)}%。2倍 → 4倍の差を評価します。入力の分割数は変更していません。</p><p class="help">実機精度や設計合否の判定ではありません。増大・振動・状態切替も確認してください。</p></div>`;
  html+=`<div class="panel"><div class="panel-head"><h3>各分割の計算状態</h3></div>${table(["倍率","流路ごとの分割数","状態","最大質量残差 [kg/s]","全体エネルギー残差 [W]"],g.levels.map(l=>[`${l.factor}倍`,Object.entries(l.cells).map(([k,v])=>`${esc(k)}: ${v}`).join(" / "),l.status==="converged"?"収束":`未成立: ${esc(l.error)}`,l.result?l.result.summary.max_mass_residual_kg_s.toExponential(2):"—",l.result?l.result.summary.global_energy_residual_w.toExponential(2):"—"]))}</div>`;
  if(g.state_changes.length)html+=`<div class="warnings"><h3>流路の状態切替</h3>${table(["流路","項目","基準 → 2倍 → 4倍"],g.state_changes.map(c=>[esc(c.name),c.field==="choked"?"チョーク":"流向（+1/0/−1）",c.values.map(v=>esc(v)).join(" → ")]))}</div>`;
  const field=$("grid-metric").value||"mass_flow_kg_s";
  const rows=g.metrics.filter(r=>r.field===field);
  if(rows.length)html+=`<div class="panel"><div class="panel-head"><h3>${esc(gridLabels[field])}<span>単位 ${esc(rows[0].unit)} / 絶対比較下限 ${fmt(rows[0].absolute_floor,4)}</span></h3></div>${table(["名称 / ID","基準","2倍","4倍","2倍→4倍の差","変化率 [%]","比較許容差","状態 / 傾向"],rows.map(r=>[`${esc(r.name)}<br><small>${esc(r.id)}</small>`,...r.values.map(v=>fmt(v,9)),fmt(r.difference,5),fmt(r.relative_percent,5),fmt(r.limit,5),`${esc(gridStatus[r.status])}<br><small>${esc(gridTrend[r.trend])}</small>`]))}</div>`;
  html+=`<div class="warnings"><ul>${g.warnings.map(w=>`<li>${esc(w)}</li>`).join("")}</ul></div>`;
  $("grid-content").innerHTML=html;$("grid-filter").hidden=!g.metrics.length;resultButtons();
}
$("run-grid").onclick=()=>action(async()=>{
  const relative=Number($("grid-relative").value);
  gridResult=null;$("grid-content").replaceChildren();$("grid-filter").hidden=true;resultButtons();
  if(!Number.isFinite(relative)||relative<.000001||relative>10)throw new Error("相対比較許容値は0.000001～10%で指定してください。");
  message("基準・2倍・4倍の分割でネットワークを計算しています…");
  gridResult=await api("/api/grid-study",{model,relative_percent:relative});renderGrid();
  message(`分割数依存性チェック: ${gridStatus[gridResult.assessment]}。`,gridResult.assessment!=="within_tolerance");
});
$("grid-metric").onchange=renderGrid;
$("grid-relative").oninput=()=>{gridResult=null;$("grid-content").replaceChildren();$("grid-filter").hidden=true;resultButtons();};
$("export-grid").onclick=()=>{if(gridResult)download("secondary-air-grid-study.json",JSON.stringify(gridResult,null,2),"application/json");};
