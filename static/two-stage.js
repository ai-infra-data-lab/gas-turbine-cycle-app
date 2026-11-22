"use strict";

const node = id => document.getElementById(id);
const form = node("design");
const numeric = (x, places, scale=1) => x == null ? "—" : (scale*x).toFixed(places);
const types = {circular:"円弧", elliptical:"楕円", cutoff:"切り落とし"};
let report;

async function api(path, payload) {
    const response = await fetch(path, payload === undefined ? {} : {
        method: "POST",
        headers: {"Content-Type":"application/json",
            "X-Cycle-Token":document.querySelector("meta[name=cycle-token]").content},
        body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "計算に失敗しました。");
    return data;
}

function labelField(label, input) {
    const container = document.createElement("label");
    container.textContent = label;
    container.append(input);
    return container;
}

function numberGrid(fields) {
    const grid = document.createElement("div");
    grid.className = "grid";
    for (const [name, label, value, min, max] of fields) {
        const input = document.createElement("input");
        Object.assign(input, {type:"number", name, value, min, required:true,
            step:name === "blade_count" ? "1" : "any"});
        if (max !== null) input.max = max;
        grid.append(labelField(label,input));
    }
    return grid;
}

function readNumbers(block,fields) {
    return Object.fromEntries(fields.map(([key])=>[key,block.querySelector(`[name="${key}"]`).valueAsNumber]));
}

function stageFields(stage, settings) {
    const section = document.createElement("details");
    const title = document.createElement("summary");
    title.textContent = `第${stage}段　流路寸法・翼のLE／TE`;
    section.append(title);
    const grid = numberGrid([...settings.sizing, ...settings.edges]);
    for (const [name, values] of Object.entries(settings.edge_types)) {
        const select = document.createElement("select");
        select.name = name;
        for (const value of values) select.add(new Option(types[value],value));
        select.value = name === "le_type" ? "elliptical" : "circular";
        grid.append(labelField(name === "le_type" ? "LE断面形状" : "TE断面形状", select));
    }
    section.append(grid);
    return section;
}

function addVanedFields(definition) {
    const inputs = node("vaned-inputs");
    for (const row of definition.rows) {
        const section = document.createElement("details");
        section.id = `vane-${row.id}`;
        const title = document.createElement("summary");
        title.textContent = row.label;
        section.append(title,numberGrid(row.fields));
        inputs.append(section);
    }
    const outlet = numberGrid(definition.outlet_fields);
    outlet.id = "vane-outlet";
    inputs.append(outlet);
}

function toggleVanedFields() {
    const enabled = node("enable-vaned").checked;
    const inputs = node("vaned-inputs");
    inputs.hidden = !enabled;
    inputs.disabled = !enabled;
}

function wallPoints(wall, offset) {
    let z = wall.start_z_m+offset, r = wall.start_r_m;
    let a = wall.start_angle_deg*Math.PI/180;
    const points = [[z,r]];
    for (const item of wall.segments) {
        if (item.length_m !== undefined) {
            z += Math.cos(a)*item.length_m;
            r += Math.sin(a)*item.length_m;
            points.push([z,r]);
        } else {
            const turn = item.sweep_deg*Math.PI/180;
            for (let i=1; i<=32; i++) {
                const b = a+turn*i/32;
                points.push([z+item.radius_m*(Math.sin(b)-Math.sin(a)),r+item.radius_m*(Math.cos(a)-Math.cos(b))]);
            }
            [z,r] = points.at(-1);
            a += turn;
        }
    }
    return points;
}

function draw(data) {
    const svg = node("layout");
    svg.replaceChildren();
    const lines = [];
    data.stages.forEach((stage,index) => {
        const offset = index ? data.return_channel.stage2_axial_offset_m : 0;
        for (const [kind,pair] of Object.entries(stage.walls)) {
            for (const wall of Object.values(pair)) {
                lines.push([wallPoints(wall,offset),kind === "impeller" ? "#2563a6" : "#0a8878"]);
            }
        }
    });
    lines.push([data.return_channel.hub_zr_m,"#7941a2"],[data.return_channel.shroud_zr_m,"#7941a2"]);
    const all = lines.flatMap(([line])=>line);
    const xs = all.map(p=>1000*p[0]), ys = all.map(p=>-1000*p[1]);
    const x0 = Math.min(...xs), y0 = Math.min(...ys);
    const w = Math.max(...xs)-x0, h = Math.max(...ys)-y0;
    const pad = .08*Math.max(w,h);
    svg.setAttribute("viewBox",[x0-pad,y0-pad,w+2*pad,h+2*pad].join(" "));
    for (const [line,color] of lines) {
        const polyline = document.createElementNS("http://www.w3.org/2000/svg","polyline");
        polyline.setAttribute("points",line.map(([z,r])=>`${1000*z},${-1000*r}`).join(" "));
        polyline.setAttribute("fill","none");
        polyline.setAttribute("stroke",color);
        polyline.setAttribute("stroke-width","2");
        polyline.setAttribute("vector-effect","non-scaling-stroke");
        svg.append(polyline);
    }
}

function fill(id, rows) {
    const body = node(id);
    body.replaceChildren();
    for (const values of rows) {
        const row = body.insertRow();
        for (const value of values) row.insertCell().textContent = value;
    }
}

function show(data) {
    fill("candidates",data.candidates.map(p=>[
        numeric(p.stage1_share,1,100)+"%", numeric(p.pressure_ratios?.[0],4),
        numeric(p.pressure_ratios?.[1],4), numeric(p.work_j_kg,3,.001), p.feasible ? "成立" : p.reason,
    ]));
    node("search").hidden = false;
    if (!data.selected) {
        node("status").textContent = "選択条件で成立する形状がありません。候補表で理由を確認してください。";
        return;
    }
    const p = data.selected;
    const equal = data.candidates.find(row=>row.stage1_share === .5 && row.feasible);
    const statement = [`第1段の配分 ${numeric(p.stage1_share,1,100)}%、圧縮比仕事 ${numeric(p.work_j_kg,3,.001)} kJ/kg。`];
    if (equal) statement.push(`均等配分は ${numeric(equal.work_j_kg,3,.001)} kJ/kg。`);
    if (data.selection !== "manual" && data.search_bounds_pct.some(limit=>p.stage1_share === limit/100)) {
        statement.push("探索範囲の端で選ばれました。");
    }
    node("selection").textContent = statement.join(" ");
    const rows = [
        ["段全圧比", (_, i)=>numeric(p.pressure_ratios[i],4)],
        ["入口チップ径 mm", s=>numeric(s.dimensions.inlet_tip_diameter_m,2,1000)],
        ["インペラー出口径 mm", s=>numeric(s.dimensions.impeller_exit_diameter_m,2,1000)],
        ["インペラー出口幅 mm", s=>numeric(s.dimensions.impeller_exit_width_m,2,1000)],
        ["ディフューザー出口径 mm", s=>numeric(s.dimensions.diffuser_exit_diameter_m,2,1000)],
        ["ディフューザー出口幅 mm", s=>numeric(s.dimensions.diffuser_exit_width_m,2,1000)],
        ["入口チップ相対マッハ数", s=>numeric(s.checks.inlet_tip_relative_mach,3)],
        ["翼枚数", s=>s.blade_edges.blade_count],
        ["LE形状・厚さ mm", s=>`${types[s.blade_edges.le_type]}、${s.blade_edges.le_thickness_mm}`],
        ["TE形状・厚さ mm", s=>`${types[s.blade_edges.te_type]}、${s.blade_edges.te_thickness_mm}`],
    ];
    fill("dimensions",rows.map(([label,pick])=>[label,...data.stages.map(pick)]));
    const duct = data.return_channel;
    node("return-note").textContent = `リターンチャネルの入口幅 ${numeric(duct.inlet_width_m,2,1000)} mm、出口幅 ${numeric(duct.outlet_width_m,2,1000)} mm。全圧損失は基準ケースの係数から ${numeric(duct.cycle_total_pressure_drop_pa,3,.001)} kPa。第2段入口は予旋回ゼロを仮定します。`;
    node("handoff-note").textContent = data.vaned_options ? "設計一式JSONに翼付き6要素の設定が含まれます。" : "";
    draw(data);
    node("summary").hidden = false;
    node("status").textContent = "計算が完了しました。";
}

function invalidate() {
    report = undefined;
    node("summary").hidden = true;
    node("search").hidden = true;
    node("status").textContent = "";
}

function save(data, name) {
    if (!data) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:"application/json"}));
    const link = document.createElement("a");
    Object.assign(link,{href:url,download:name});
    link.click();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
}
node("report-out").onclick = ()=>save(report,report?.vaned_options ? "two-stage-vaned-design.json" : "two-stage-design.json");
node("cycle-out").onclick = ()=>save(report?.cycle_case,"two-stage-cycle.json");
node("return-out").onclick = ()=>save(report?.return_channel,"return-channel-sizing.json");
for (const i of [1,2]) node(`stage${i}-out`).onclick = ()=>save(report?.stages?.[i-1],`stage-${i}-sizing.json`);

let performance;
const performanceForm = node("performance-form");
const performanceFile = node("performance-file");
const performanceGo = node("performance-go");
const performanceStatus = node("performance-status");
const performanceDesigns = ["equal", "75-percent"];
const sourceTypes = {cfd:"CFD", test:"試験", synthetic:"仮想"};

function clearPerformance() {
    performance = undefined;
    node("performance-result").hidden = true;
    performanceStatus.textContent = "";
}

function showPerformance(data) {
    const point = data.performance.conditions;
    node("performance-operation").textContent = [
        `共通の圧縮機入口条件 ${numeric(point.inlet_total_temperature_k,2)} K、${numeric(point.inlet_total_pressure_pa,3,.001)} kPa（絶対圧）`,
        `${point.air_mass_flow_kg_s} kg/s、${point.shaft_speed_rpm.toLocaleString("ja-JP")} rpm`,
    ].join(" · ");
    const designs = performanceDesigns.map(key=>data.performance.designs[key]);
    const cases = performanceDesigns.map(key=>data.cycle_cases[key]);
    const rows = [
        ["出典", ...designs.map(d=>`${sourceTypes[d.source_kind]} / ${d.source_reference}`), "—"],
        ["静翼の全圧損失", ...designs.map(d=>d.pressure_retentions ? "要素別に反映" : "段効率に含める"), "—"],
    ];
    const values = [
        ["第1段の対数全圧比配分 %", (m,c)=>c.stages[0].pressure_log_fraction, 2, 100],
        ["第1段全圧比", (m,c)=>c.stages[0].pressure_ratio, 4],
        ["第2段全圧比", (m,c)=>c.stages[1].pressure_ratio, 4],
        ["圧縮機全圧比", (m,c)=>c.overall_pressure_ratio, 4],
        ["第1段全圧効率 %", (m,c)=>c.stages[0].isentropic_efficiency, 2, 100],
        ["第2段全圧効率 %", (m,c)=>c.stages[1].isentropic_efficiency, 2, 100],
        ["圧縮比仕事 kJ/kg", (m,c)=>c.specific_work_j_kg, 3, .001],
        ["圧縮機動力 kW", m=>m.compressor_power_w, 3, .001],
        ["正味発電出力 kW", m=>m.net_electric_power_w, 3, .001],
        ["燃料流量 kg/h", m=>m.fuel_mass_flow_kg_s, 3, 3600],
        ["正味燃料消費率 g/kWh", m=>m.specific_fuel_consumption_kg_kwh, 2, 1000],
    ];
    for (const [label, pick, places, scale] of values) {
        const [equal, seventyFive] = cases.map(c=>pick(c.result.metrics,c.result.stage_results.compressor));
        const delta = equal == null || seventyFive == null ? null : seventyFive-equal;
        const difference = numeric(delta,places,scale);
        rows.push([label, numeric(equal,places,scale), numeric(seventyFive,places,scale),
            delta > 0 && Number(difference) !== 0 ? `+${difference}` : difference]);
    }
    fill("performance-metrics",rows);
    node("performance-result").hidden = false;
    const completed = cases.every(c=>c.result.status === "success");
    performanceStatus.textContent = completed ? "比較が完了しました。" :
        "比較が完了しました。正味発電が成立しない条件の燃料消費率は「—」で表示します。";
}

node("base").addEventListener("change",clearPerformance);
performanceFile.addEventListener("change",clearPerformance);
performanceForm.addEventListener("submit",async event=>{
    event.preventDefault();
    clearPerformance();
    const file = performanceFile.files[0];
    if (!file) return;
    const base = node("base");
    const caseId = base.value;
    performanceFile.disabled = performanceGo.disabled = true;
    performanceStatus.textContent = "性能値を反映して比較しています。";
    try {
        if (file.size > 1_000_000) throw new Error("JSONは1MB以内にしてください。");
        const performanceDocument = JSON.parse(await file.text());
        const data = await api("/api/sizing/two-stage-performance",{case_id:caseId, document:performanceDocument});
        if (base.value === caseId) {
            showPerformance(data);
            performance = data;
        }
    } catch (error) {
        if (base.value === caseId) performanceStatus.textContent = error.message;
    } finally {
        performanceFile.disabled = performanceGo.disabled = false;
    }
});
node("performance-report-out").onclick = ()=>save(performance,"two-stage-performance-comparison.json");
node("performance-equal-out").onclick = ()=>save(performance?.cycle_cases.equal,"two-stage-cycle-equal.json");
node("performance-75-out").onclick = ()=>save(performance?.cycle_cases["75-percent"],"two-stage-cycle-75-percent.json");

form.addEventListener("input",invalidate);
node("enable-vaned").addEventListener("change",toggleVanedFields);
form.addEventListener("invalid",event=>{
    const stage = event.target.closest("details");
    if (stage) stage.open = true;
},true);
form.addEventListener("submit",async event=>{
    event.preventDefault();
    invalidate();
    const groups = [...node("stage-inputs").children];
    const payload = {
        case_id:node("base").value, stage1_share:node("share").value === "" ? null : node("share").valueAsNumber/100,
        search_min_pct:node("search-min").valueAsNumber, search_max_pct:node("search-max").valueAsNumber,
        stages:groups.map(block=>readNumbers(block,settings.sizing)),
        blade_edges:groups.map(block=>({...readNumbers(block,settings.edges),
            le_type:block.querySelector('[name="le_type"]').value, te_type:block.querySelector('[name="te_type"]').value})),
    };
    if (node("enable-vaned").checked) {
        payload.vaned_options = Object.fromEntries(settings.vaned.rows.map(row=>[row.id,readNumbers(node(`vane-${row.id}`),row.fields)]));
        payload.vaned_options.outlet = readNumbers(node("vane-outlet"),settings.vaned.outlet_fields);
    }
    const elements = [...form.elements];
    const disabled = elements.map(element=>element.disabled);
    elements.forEach(element=>{element.disabled=true;});
    node("status").textContent = "圧力比の配分と子午面を計算しています。";
    try {
        report = await api("/api/sizing/two-stage",payload);
        show(report);
    } catch (error) {
        node("status").textContent = error.message;
    } finally {
        elements.forEach((element,index)=>{element.disabled=disabled[index];});
    }
});

let settings;
Promise.all([api("/api/sizing/two-stage-fields"),api("/api/cases")]).then(([fields,cases])=>{
    settings = fields;
    for (const c of cases) node("base").add(new Option(c.name,c.id));
    for (const i of [1,2]) node("stage-inputs").append(stageFields(i,settings));
    addVanedFields(settings.vaned);
    toggleVanedFields();
    node("go").disabled = false;
    performanceGo.disabled = false;
}).catch(error=>{
    node("status").textContent = error.message;
    performanceStatus.textContent = error.message;
});
