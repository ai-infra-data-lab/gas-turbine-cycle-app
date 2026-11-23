"use strict";

const $ = id => document.getElementById(id);
const form = $("exchange");
const number = (value, digits=3) => value == null ? "—" :
    Number(value).toLocaleString("ja-JP", {maximumFractionDigits:digits});
let reference;
let files = [];

async function api(path, body) {
    const response = await fetch(path, body === undefined ? {} : {
        method:"POST",
        headers:{"Content-Type":"application/json",
            "X-Cycle-Token":document.querySelector("meta[name=cycle-token]").content},
        body:JSON.stringify(body),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "連携計算が失敗しました。");
    return data;
}

function clearResults() {
    files = [];
    $("results").hidden = true;
    $("status").textContent = "";
}

function showTools() {
    clearResults();
    const tool = $("tool").value;
    $("cycle-label").hidden = $("demo-label").hidden = tool === "oil";
    $("reference-fields").hidden = !["cooling","rotor"].includes(tool);
    $("load-rotating-sample").hidden = tool !== "cooling";
    $("load-coupled-sample").hidden = tool !== "cooling";
    $("reference-title").textContent = tool === "cooling" ? "軸流タービン連携の結果JSON" : "給油の比較結果JSON（任意）";
    $("reference-file").value = "";
    $("oil-condition-label").hidden = true;
    $("settings").value = "";
    reference = undefined;
}

async function readFile(input) {
    if (!input.files.length) return undefined;
    const file = input.files[0];
    if (file.size > 1_000_000) throw new Error("JSONは1MB以内にしてください。");
    return JSON.parse(await file.text());
}

function renderSensitivity(cases) {
    const parameters = {
        outer_pressure_pa: {name:"外周静圧", factor:0.001, unit:" kPa"},
        swirl_ratio: {name:"旋回比", factor:1, unit:""},
        inner_radius_m: {name:"受圧内半径", factor:1000, unit:" mm"},
    };
    const joints = {spline:"スプライン", curvic:"カービック"};
    const body = $("sensitivity-rows");
    body.replaceChildren();
    for (const variant of cases) {
        for (const point of [variant.baseline, ...variant.points]) {
            let label = "基準";
            if (point.parameter) {
                const parameter = parameters[point.parameter];
                const side = {front:"前面", back:"背面"}[point.side] || "";
                const delta = (point.offset > 0 ? "+" : "") + number(point.offset * parameter.factor);
                label = `第${point.stage}段・領域${point.cavity} ${side}${parameter.name} ${delta}${parameter.unit}`;
            }
            const row = body.insertRow();
            const jointLoads = point.joints.map(joint =>
                `${joints[joint.kind]}（${number(joint.x_m * 1000)} mm） ${number(joint.tension_n)}`).join(" / ");
            const values = [number(variant.stage1_share * 100) + "%", label,
                ...point.stages.map(stage => number(stage.force_n)),
                number(point.bearing_reaction_n), number(point.bearing_delta_n ?? 0), jointLoads || "—"];
            for (const value of values) row.insertCell().textContent = value;
        }
    }
}

function outputTable(tool, data) {
    const rows = [];
    const add = (target,label,value,unit="",digits=3) =>
        rows.push([target,label,typeof value === "string" ? value : number(value,digits)+(value == null ? "" : unit)]);
    const addFile = (label, filename, content) => files.push({label,filename,content});
    addFile("連携結果一式",tool+"-report.json",data);
    const sensitivity = tool === "thrust-sensitivity";
    $("summary-table").hidden = sensitivity;
    $("sensitivity-table").hidden = !sensitivity;
    if (sensitivity) renderSensitivity(data.cases);
    if (tool === "thrust") {
        for (const item of data.stages) {
            add(item.name,"入口静圧",item.inlet_static_pa/1000," kPa");
            add(item.name,"出口静圧",item.outlet_static_pa/1000," kPa");
            add(item.name,"段軸力",item.force_n," N");
        }
        add("ロータ","スラスト軸受反力",data.bearing_reaction.force_n," N");
        data.joints.forEach((item,i)=>{
            add("継手 "+(i+1),"軸力（引張正）",item.tension_n," N");
            addFile("継手 "+(i+1)+"の強度ケース","shaft-joint-"+(i+1)+".json",item.strength_case);
        });
    }
    if (tool === "thrust-seal") {
        Object.entries(data.cases).forEach(([name, item], i) => {
            for (const side of item.cavity_inputs) {
                const label = side.side === "front" ? "前面" : "背面";
                add(name,`第${side.stage}段 ${label}領域${side.region}の外周絶対静圧`,side.outer_pressure_pa/1000," kPa");
            }
            for (const stage of item.thrust.stages) add(name,stage.name+"の軸力",stage.force_n," N");
            add(name,"スラスト軸受反力",item.thrust.bearing_reaction.force_n," N");
            item.thrust.joints.forEach((joint,j) => {
                add(name,`継手 ${j+1}・軸力（引張正）`,joint.tension_n," N");
                addFile(`${name}・継手 ${j+1}`,`shaft-joint-${i+1}-${j+1}.json`,joint.strength_case);
            });
            addFile(name+"の漏れ回路",`cavity-network-${i+1}.json`,item.network_result.model);
        });
    }
    if (tool === "combustor") {
        const c = data.per_can;
        add("1缶","燃料流量",c.fuel_kg_s," kg/s",6);
        add("1缶","出口全温",c.outlet_total_temperature_k," K");
        add("1缶","出口絶対全圧",c.outlet_total_pressure_pa/1000," kPa");
        add("1缶","全圧損失",c.total_pressure_loss_pa/1000," kPa");
        add("1缶","全圧損失率",c.total_pressure_loss_fraction*100," %",4);
        add("1缶","計算基準点のマッハ数",c.inlet_mach,"",5);
        for (const [key,value] of Object.entries(c.zones_kg_s)) {
            const names = {primary:"一次空気",secondary:"二次空気",dilution:"希釈空気",liner_cooling:"ライナ冷却空気"};
            add("1缶",names[key],number(value,6)+" kg/s · "+number(c.air_split[key]*100,3)+" %");
        }
        const profile = data.thermal?.outlet_profile;
        if (profile) {
            profile.sectors.forEach(row => add(row.id,"出口全温",row.total_temperature_k," K"));
            add("出口分布","パターンファクター",profile.pattern_factor,"",5);
        }
        for (const liner of data.thermal?.liner_segments || []) {
            add(liner.id,"ライナ高温側温度",liner.hot_wall_temperature_k," K");
            add(liner.id,"ライナ冷却側温度",liner.cold_wall_temperature_k," K");
            add(liner.id,"材料温度余裕",liner.temperature_margin_k," K");
        }
    }
    if (tool === "turbine") {
        Object.entries(data.cases).forEach(([name,item],i)=>{
            add(name,"ガス出力",item.analysis.summary.power/1000," kW");
            for (const stage of item.analysis.stages) {
                add(name,"第"+stage.stage+"段 比仕事",stage.work/1000," kJ/kg");
                add(name,"第"+stage.stage+"段 動翼出口軸流速度",stage.outlet.axial_velocity," m/s");
            }
            add(name,"排気ディフューザーの静圧変化",item.diffuser_static_recovery_pa/1000," kPa");
            if (item.exhaust_diffuser) {
                add(name,"排気ディフューザー入口面積",item.exhaust_diffuser.inlet_area_m2," m²",7);
                add(name,"排気ディフューザー出口面積",item.exhaust_diffuser.outlet_area_m2," m²",7);
                add(name,"排気ディフューザーの全圧損失",item.exhaust_diffuser.total_pressure_loss_pa/1000," kPa");
            }
            addFile(name+"の軸流タービン設計","axial-turbine-"+(i+1)+".json",
                {format:"axial-turbine-designer",bundle_version:6,design:item.design});
        });
    }
    if (tool === "cooling") {
        for (const item of data.injections) {
            add(item.station,"冷却流量",item.coolant_flow_kg_s," kg/s",6);
            add(item.station,"冷却全温",item.coolant_total_temperature_k," K");
        }
        if (data.frame_transfer) add("回転流路","軸仕事率",data.network_result.summary.shaft_work_w," W");
        const feedback = {design_point_once:"1回反映済み", coupled_design_point:"反復収束",
                          coupled_not_converged:"反復連携が未収束"}[data.cycle_feedback] || data.cycle_feedback;
        add("Cycle Studio","運転点への反映",feedback);
        if (data.coupling?.iterations.length) {
            const last = data.coupling.iterations.at(-1);
            add("Cycle Studio","反復回数",data.coupling.iterations.length," 回");
            add("Cycle Studio","境界静圧・供給全圧の最大差",last.max_pressure_difference_pa," Pa",5);
            add("Cycle Studio","境界全温の最大差",last.max_temperature_difference_k," K",6);
            add("Cycle Studio","最大流量変化",last.max_flow_change_kg_s," kg/s",10);
            data.coupling.iterations.forEach((step,index) => {
                const boundary = step.boundaries?.reduce((worst,row) =>
                    Math.abs(row.pressure_difference_pa) > Math.abs(worst.pressure_difference_pa) ? row : worst);
                add(`反復 ${index+1}`,"最大境界差",
                    `${number(step.max_pressure_difference_pa,5)} Pa · ${number(step.max_temperature_difference_k,6)} K · ` +
                    `${number(step.max_flow_change_kg_s,10)} kg/s${boundary ? " · 最大圧差の位置 " + boundary.station : ""}`);
            });
        }
        addFile("二次空気ネットワークの入力","secondary-air-network.json",data.network_result.model);
    }
    if (tool === "oil") {
        Object.entries(data.conditions).forEach(([name,item],i)=>{
            add(name,"ポンプ作動流量",item.pump_flow_lpm," L/min");
            for (const [bearing,feed] of Object.entries(item.bearings)) {
                add(name+" / "+bearing,"給油流量余裕",feed.flow_margin_lpm," L/min");
                add(name+" / "+bearing,"入口ゲージ圧余裕",feed.pressure_margin_kpa," kPa");
            }
            addFile(name+"の油配管入力","oil-network-"+(i+1)+".json",item.model);
        });
    }
    if (tool === "rotor") {
        Object.entries(data.cases).forEach(([name,item],i)=>{
            add(name,"運転回転数",item.operating_rpm," rpm");
            add(name,"最も近い危険速度",item.nearest_critical?.rpm," rpm");
            add(name,"危険速度余裕",item.separation_margin_fraction == null ? null : item.separation_margin_fraction*100," %");
            add(name,"運転点の同期応答片振幅",item.operating_amplitude_um," µm");
            for (const feed of item.oil_supply) add(name+" / "+feed.bearing_id,"給油判定",feed.passes ? "要求値を満たす" : "要求値を下回る");
            addFile(name+"のRotorLabモデル","rotor-model-"+(i+1)+".json",item.model);
        });
    }
    if (data.cycle_case) addFile("通常のサイクル画面で読めるケース","cycle-case.json",data.cycle_case);
    const body = $("summary");
    body.replaceChildren();
    for (const values of rows) {
        const row = body.insertRow();
        for (const value of values) row.insertCell().textContent = value;
    }
    $("output-file").replaceChildren(...files.map((item,i)=>new Option(item.label,String(i))));
    $("result-note").textContent = data.source_cycle ?
        "入力元サイクル "+data.source_cycle.run_id+" · この計算は設計点の概算です。" :
        "給油圧はゲージ圧。入力した油物性とポンプ曲線で評価しています。";
    $("results").hidden = false;
}

$("tool").addEventListener("change",showTools);
form.addEventListener("input",clearResults);
async function loadSample(variant="") {
    clearResults();
    const tool = $("tool").value;
    try {
        const sample = await api("/api/design-tools-example?tool="+tool+variant);
        if ($("tool").value === tool) $("settings").value = JSON.stringify(sample,null,2);
    } catch (error) {
        $("status").textContent = error.message;
    }
}
$("load-sample").addEventListener("click",()=>loadSample());
$("load-rotating-sample").addEventListener("click",()=>loadSample("&variant=rotating"));
$("load-coupled-sample").addEventListener("click",()=>loadSample("&variant=coupled"));
$("reference-file").addEventListener("change", async ()=>{
    const tool = $("tool").value;
    const file = $("reference-file").files[0];
    reference = undefined;
    $("oil-condition-label").hidden = true;
    try {
        const report = await readFile($("reference-file"));
        if ($("tool").value !== tool || $("reference-file").files[0] !== file) return;
        if (!report) return;
        const expected = tool === "oil" || tool === "rotor" ? "oil-bearing-supply-comparison" : "axial-turbine-cycle-handoff";
        if (report.format !== expected) throw new Error("連携先に対応する結果JSONを選んでください。");
        reference = report;
        if (tool === "rotor") {
            $("oil-condition").replaceChildren(...Object.keys(report.conditions).map(key=>new Option(key,key)));
            $("oil-condition-label").hidden = false;
        }
    } catch (error) {
        $("status").textContent = error.message;
    }
});
form.addEventListener("submit",async event=>{
    event.preventDefault();
    clearResults();
    const tool = $("tool").value;
    const controls = [...form.elements];
    controls.forEach(input=>{input.disabled=true;});
    $("status").textContent = "計算しています。";
    try {
        const settings = JSON.parse($("settings").value);
        const cycle = tool === "oil" ? undefined : await readFile($("cycle-file"));
        const demo = $("demo-cycle").checked;
        if (tool !== "oil" && !demo && !cycle) throw new Error("サイクルJSONを選ぶか、仮想サイクルを指定してください。");
        if (tool === "cooling") {
            settings.turbine = reference || settings.turbine;
            if (!settings.turbine) throw new Error("対応するタービン連携の結果JSONを選んでください。");
        }
        if (tool === "rotor" && reference) {
            const selected = reference.conditions[$("oil-condition").value];
            for (const variant of Object.values(settings.cases)) {
                variant.oil_supply = {temperature_c:selected.temperature_c,bearings:selected.bearings};
            }
        }
        outputTable(tool,await api("/api/design-tools",{tool,cycle,demo_cycle:demo,settings}));
        $("status").textContent = "計算が完了しました。";
    } catch (error) {
        $("status").textContent = error.message;
    } finally {
        controls.forEach(input=>{input.disabled=false;});
    }
});
$("download").addEventListener("click",()=>{
    const output = files[Number($("output-file").value)];
    if (!output) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(output.content,null,2)],{type:"application/json"}));
    const link = document.createElement("a");
    Object.assign(link,{href:url,download:output.filename});
    link.click();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
});
const requestedTool = new URLSearchParams(window.location.search).get("tool");
if (Array.from($("tool").options).some(option => option.value === requestedTool)) {
    $("tool").value = requestedTool;
}
showTools();
