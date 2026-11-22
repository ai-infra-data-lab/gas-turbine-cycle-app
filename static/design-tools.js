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

function outputTable(tool, data) {
    const rows = [];
    const add = (target,label,value,unit="",digits=3) =>
        rows.push([target,label,typeof value === "string" ? value : number(value,digits)+(value == null ? "" : unit)]);
    const addFile = (label, filename, content) => files.push({label,filename,content});
    addFile("連携結果一式",tool+"-report.json",data);
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
    if (tool === "combustor") {
        const c = data.per_can;
        add("1缶","燃料流量",c.fuel_kg_s," kg/s",6);
        add("1缶","出口全温",c.outlet_total_temperature_k," K");
        add("1缶","出口絶対全圧",c.outlet_total_pressure_pa/1000," kPa");
        add("1缶","全圧損失",c.total_pressure_loss_pa/1000," kPa");
        for (const [key,value] of Object.entries(c.zones_kg_s)) {
            const names = {primary:"一次空気",secondary:"二次空気",dilution:"希釈空気",liner_cooling:"ライナ冷却空気"};
            add("1缶",names[key],value," kg/s",6);
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
        add("Cycle Studio","運転点への反映",data.cycle_case ? "1回反映済み" : data.cycle_feedback);
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
showTools();
