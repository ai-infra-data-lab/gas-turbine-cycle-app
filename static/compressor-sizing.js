"use strict";

const byId = id => document.getElementById(id);
const form = byId("sizing-form");
const submit = byId("calculate");
const download = byId("download");
const select = byId("cycle-case");
const error = byId("error");
let report;
let analysis;
let designRequest;

async function request(path, payload) {
    const options = payload === undefined ? {} : {
        method: "POST",
        headers: {"Content-Type": "application/json",
                  "X-Cycle-Token": document.querySelector("meta[name=cycle-token]").content},
        body: JSON.stringify(payload),
    };
    const response = await fetch(path, options);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "通信に失敗しました。");
    return data;
}

function invalidate() {
    report = undefined;
    download.disabled = true;
    byId("result").hidden = true;
    byId("diffuser").hidden = true;
    invalidateAnalysis();
}

function invalidateAnalysis() {
    analysis = undefined;
    byId("diffuser-results").hidden = true;
    byId("diffuser-status").textContent = "";
}

function points(wall) {
    let z = wall.start_z_m;
    let r = wall.start_r_m;
    let angle = wall.start_angle_deg * Math.PI/180;
    const result = [[z,r]];
    for (const part of wall.segments) {
        if (part.length_m !== undefined) {
            z += part.length_m*Math.cos(angle);
            r += part.length_m*Math.sin(angle);
        } else {
            const sweep = part.sweep_deg*Math.PI/180;
            for (let i=1; i<=24; i++) {
                const t = angle+sweep*i/24;
                result.push([z+part.radius_m*(Math.sin(t)-Math.sin(angle)),
                             r+part.radius_m*(Math.cos(angle)-Math.cos(t))]);
            }
            [z,r] = result.at(-1);
            angle += sweep;
        }
        result.push([z,r]);
    }
    return result.map(([z,r]) => [1000*z,-1000*r]);
}

function plot(walls) {
    const svg = byId("plot");
    svg.replaceChildren();
    const lines = Object.values(walls).flatMap(part => Object.values(part).map(points));
    const all = lines.flat();
    const xs = all.map(p => p[0]);
    const ys = all.map(p => p[1]);
    const xmin = Math.min(...xs), ymin = Math.min(...ys);
    const width = Math.max(...xs)-xmin, height = Math.max(...ys)-ymin;
    const margin = .12*Math.max(width,height);
    svg.setAttribute("viewBox", [xmin-margin,ymin-margin,width+2*margin,height+2*margin].join(" "));
    lines.forEach((line, index) => {
        const element = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
        element.setAttribute("points", line.map(p => p.join(",")).join(" "));
        element.setAttribute("fill", "none");
        element.setAttribute("stroke", index < 2 ? "#2563a6" : "#0a8878");
        element.setAttribute("stroke-width", "2");
        element.setAttribute("vector-effect", "non-scaling-stroke");
        svg.append(element);
    });
}

function render(data) {
    const d = data.dimensions, c = data.checks, t = data.cycle_targets;
    const mm = value => (value*1000).toFixed(2)+" mm";
    const speed = value => value.toFixed(1)+" m/s";
    const rows = [
        ["入口ハブ径", mm(d.inlet_hub_diameter_m)],
        ["入口チップ径", mm(d.inlet_tip_diameter_m)],
        ["インペラー出口径", mm(d.impeller_exit_diameter_m)],
        ["インペラー出口幅", mm(d.impeller_exit_width_m)],
        ["ディフューザー出口径", mm(d.diffuser_exit_diameter_m)],
        ["ディフューザー出口幅", mm(d.diffuser_exit_width_m)],
        ["インペラー出口周速", speed(c.tip_speed_m_s)],
        ["入口チップ相対マッハ数", c.inlet_tip_relative_mach.toFixed(3)],
        ["インペラー出口相対マッハ数", c.impeller_exit_relative_mach.toFixed(3)],
        ["インペラー出口絶対流速", speed(data.sizing_states.impeller_exit.velocity_m_s),
            speed(t.impeller_exit_speed_m_s)],
        ["ディフューザー出口絶対流速", speed(data.sizing_states.diffuser_exit.velocity_m_s),
            speed(t.diffuser_exit_speed_m_s)],
        ["ディフューザー静圧回復係数", c.cp_at_sized_velocities.toFixed(4), t.diffuser_cp.toFixed(4)],
    ];
    const body = byId("values");
    body.replaceChildren();
    for (const [label, sized, cycle = "—"] of rows) {
        const row = body.insertRow();
        for (const value of [label,sized,cycle]) row.insertCell().textContent = value;
    }
    byId("source").textContent = data.source.name + " ／ " + t.mass_flow_kg_s.toFixed(3) +
        " kg/s ／ " + t.shaft_speed_rpm + " rpm";
    plot(data.walls);
    byId("result").hidden = false;
    byId("diffuser").hidden = false;
}

form.addEventListener("input", invalidate);
form.addEventListener("submit", async event => {
    event.preventDefault();
    invalidate();
    error.textContent = "";
    const fields = [...form.querySelectorAll("input")];
    const assumptions = Object.fromEntries(fields.map(field => [field.name, field.valueAsNumber]));
    for (const field of [submit,select,...fields]) field.disabled = true;
    try {
        designRequest = {case_id: select.value, assumptions};
        report = await request("/api/sizing/calculate", designRequest);
        render(report);
        download.disabled = false;
    } catch (err) {
        error.textContent = err.message;
    } finally {
        for (const field of [submit,select,...fields]) field.disabled = false;
    }
});

function writeJson(documentValue, filename) {
    const url = URL.createObjectURL(new Blob([JSON.stringify(documentValue,null,2)], {type:"application/json"}));
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
}

download.addEventListener("click", () => {
    if (report) writeJson(report, "compressor-sizing.json");
});

function fillRows(body, rows) {
    body.replaceChildren();
    for (const values of rows) {
        const row = body.insertRow();
        for (const value of values) row.insertCell().textContent = value;
    }
}

function renderAnalysis(data) {
    const c = data.cycle_comparison;
    const match = data.fixed_geometry;
    const fixed = match.cycle_comparison;
    const original = c ?? fixed;
    const value = (x, digits, factor=1) => x == null ? "—" : (x*factor).toFixed(digits);
    const k = x => value(x, 3, .001);
    fillRows(byId("diffuser-values"), [
        ["段の全圧比", value(match.achieved_pressure_ratio, 4), value(match.target_pressure_ratio, 4),
            value(c?.updated_pressure_ratio, 4), value(fixed?.updated_pressure_ratio, 4)],
        ["全圧損失 kPa", k(data.pressure_loss_pa), k(original?.original_pressure_loss_pa),
            k(c?.updated_pressure_loss_pa), k(fixed?.updated_pressure_loss_pa)],
        ["静圧回復係数", value(data.pressure_recovery_coefficient, 4),
            value(original?.original_diffuser_cp, 4), value(c?.updated_diffuser_cp, 4), value(fixed?.updated_diffuser_cp, 4)],
        ["圧縮機比仕事 kJ/kg", k(match.euler_work_j_kg), k(original?.original_compressor_work_j_kg),
            k(c?.updated_compressor_work_j_kg), k(fixed?.updated_compressor_work_j_kg)],
        ["正味発電出力 kW", "—", k(original?.original_net_output_w),
            k(c?.updated_net_output_w), k(fixed?.updated_net_output_w)],
    ]);
    byId("diffuser-grid").textContent =
        `形状解析の全圧損失係数 ζ ${value(data.loss_coefficient, 4)} ／ 最大マッハ数 ${value(data.peak_mach, 3)} ／ 32→64分割での出口全圧差 ${value(data.grid_pressure_delta_pa, 3)} Pa`;
    const messages = [];
    if (data.pressure_recovery_coefficient <= 0) messages.push("この条件では静圧回復が得られません。");
    if (c) {
        messages.push(`段圧力比を固定すると、ディフューザー入口全圧は形状解析から ${k(c.diffuser_inlet_total_pressure_shift_pa)} kPa 変わります。`);
    } else {
        messages.push("段圧力比固定のサイクルを計算できませんでした。 " + data.cycle_error);
    }
    if (fixed) {
        messages.push(`形状と比仕事を固定した場合、目標に対する段圧力比の差は ${value(match.pressure_ratio_delta, 4)} です。`);
        const r = match.residuals;
        messages.push(`接続残差はディフューザー入口全圧 ${r.diffuser_inlet_total_pressure_pa.toExponential(2)} Pa、出口全圧 ${r.diffuser_outlet_total_pressure_pa.toExponential(2)} Pa、インペラー比仕事 ${r.impeller_work_j_kg.toExponential(2)} J/kg です。`);
    } else {
        messages.push("形状固定のサイクルを計算できませんでした。 " + match.cycle_error);
    }
    byId("cycle-note").textContent = messages.join(" ");
    fillRows(byId("diffuser-stations"), data.states.map(s => [
        value(s.radius_m, 2, 1000), value(s.width_m, 2, 1000), k(s.total_pressure_pa),
        k(s.static_pressure_pa), value(s.radial_velocity_m_s, 1), value(s.swirl_velocity_m_s, 1), value(s.mach, 3),
    ]));
    byId("cycle-download").disabled = !c;
    byId("geometry-cycle-download").disabled = !fixed;
    byId("diffuser-results").hidden = false;
}

const darcyField = byId("darcy");
const analyzeButton = byId("analyze-diffuser");
darcyField.addEventListener("input", invalidateAnalysis);
byId("diffuser-form").addEventListener("submit", async event => {
    event.preventDefault();
    if (!report) return;
    invalidateAnalysis();
    byId("diffuser-status").textContent = "形状損失を計算しています。";
    const originalDesign = report;
    const payload = {...designRequest, darcy_friction_factor: darcyField.valueAsNumber};
    darcyField.disabled = true;
    analyzeButton.disabled = true;
    try {
        const data = await request("/api/sizing/diffuser", payload);
        if (report !== originalDesign) return;
        analysis = data;
        renderAnalysis(analysis);
        byId("diffuser-status").textContent = "計算が完了しました。";
    } catch (err) {
        if (report === originalDesign) byId("diffuser-status").textContent = err.message;
    } finally {
        darcyField.disabled = false;
        analyzeButton.disabled = false;
    }
});
byId("diffuser-download").addEventListener("click", () => {
    if (analysis) writeJson(analysis, "diffuser-analysis.json");
});
byId("cycle-download").addEventListener("click", () => {
    const cycle = analysis?.cycle_comparison?.updated_case;
    if (cycle) writeJson(cycle, "cycle-with-diffuser-loss.json");
});
byId("geometry-cycle-download").addEventListener("click", () => {
    const cycle = analysis?.fixed_geometry?.cycle_comparison?.updated_case;
    if (cycle) writeJson(cycle, "cycle-with-fixed-compressor-geometry.json");
});

Promise.all([request("/api/sizing/fields"), request("/api/cases")]).then(([fields, cases]) => {
    for (const entry of cases) select.add(new Option(entry.name, entry.id));
    for (const [key,title,value,min,max] of fields) {
        const label = document.createElement("label");
        label.textContent = title;
        const input = document.createElement("input");
        Object.assign(input, {name:key, type:"number", value, min, max, step:"any", required:true});
        label.append(input);
        byId("assumptions").append(label);
    }
    select.disabled = false;
    submit.disabled = false;
}).catch(err => { error.textContent = err.message; });
