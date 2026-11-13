"""Build an offline room overview and a shareable plan-and-axon preview."""

import base64
import binascii
import html
import json
import re
from io import BytesIO
from math import cos, pi, sin
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image, ImageDraw, ImageOps
from shapely import union_all
from shapely.geometry import Polygon, box

from model import InputError
from visual import font, shorten


HTML_NAME = "整区总览.html"
PNG_NAME = "整区总览.png"
ZONE_COLORS = ("#86aa92", "#94a9cf", "#cc9b70", "#b4a0c8", "#80aab1", "#c99993", "#b3aa78", "#99b0aa")
PAPER = "#f3f2ed"
INK = "#263a34"
MUTED = "#62726a"
STROKE = "#d6ddd3"
PNG_DATA_PREFIX = "data:image/png;base64,"


SHELL = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:">
<title>__TITLE__ · 整区总览</title>
<style>
:root { color-scheme:light; --paper:#f3f2ed; --card:#fffefa; --ink:#263a34; --muted:#62726a; --line:#d6ddd3; --accent:#296356; --pick:#a7552d; }
* { box-sizing:border-box; }
body { margin:0; color:var(--ink); background:var(--paper); font:15px/1.55 system-ui,"Microsoft YaHei UI","PingFang SC",sans-serif; }
button,input { font:inherit; }
button { cursor:pointer; }
button:focus-visible,input:focus-visible { outline:3px solid #a4bcac; outline-offset:2px; }
main { max-width:1580px; margin:auto; padding:clamp(18px,4vw,54px); }
header { display:flex; gap:24px; align-items:end; justify-content:space-between; margin-bottom:26px; }
.heading { min-width:0; }
.label { margin:0 0 4px; color:var(--accent); font-size:14px; font-weight:750; }
h1,h2,h3,p { margin:0; }
h1 { font-size:clamp(29px,4vw,55px); line-height:1.1; font-weight:790; overflow-wrap:anywhere; }
h2 { font-size:19px; line-height:1.3; }
h3 { font-size:17px; }
.description { margin-top:12px; color:var(--muted); max-width:68ch; overflow-wrap:anywhere; }
.metrics { display:flex; gap:10px; flex:0 0 auto; }
.metric { min-width:122px; padding:9px 13px; background:#e6ece3; border-radius:9px; }
.metric b { display:block; white-space:nowrap; font-size:20px; font-variant-numeric:tabular-nums; }
.metric span { color:var(--muted); font-size:12px; }
.views { display:grid; grid-template-columns:minmax(0,1.15fr) minmax(0,1fr); gap:15px; align-items:stretch; }
.card { min-width:0; background:var(--card); border:1px solid var(--line); border-radius:15px; box-shadow:0 2px 8px #25393008; }
.view { padding:17px 17px 12px; }
.view-head { display:flex; flex-wrap:wrap; align-items:baseline; justify-content:space-between; gap:4px 8px; margin-bottom:8px; }
.view-head p { color:var(--muted); font-size:12px; }
.viewport { display:block; width:100%; height:clamp(280px,31vw,500px); background:#f7f7f2; border-radius:10px; }
.legend { display:flex; flex-wrap:wrap; gap:6px 13px; margin-top:10px; min-height:23px; }
.legend-item { display:inline-flex; gap:6px; align-items:center; color:var(--muted); font-size:12px; overflow-wrap:anywhere; }
.swatch { width:10px; height:10px; border-radius:3px; flex-shrink:0; border:1px solid #263a3428; }
.workspace { display:grid; grid-template-columns:minmax(0,1.27fr) minmax(0,.83fr); gap:15px; margin-top:15px; align-items:start; }
.index,.detail,.needs { padding:17px; }
.index-head { display:flex; flex-wrap:wrap; gap:10px; align-items:center; justify-content:space-between; }
.search { min-width:160px; flex:1 1 220px; max-width:360px; background:#f7f7f2; border:1px solid #c7d2c7; border-radius:8px; padding:8px 10px; color:var(--ink); }
.index-note { margin-top:9px; color:var(--muted); font-size:12px; }
.results { margin-top:8px; max-height:375px; overflow:auto; }
.item { width:100%; display:grid; grid-template-columns:70px minmax(0,1fr) auto; gap:8px; align-items:center; padding:8px 7px; border:0; border-bottom:1px solid #e8ece5; background:transparent; text-align:left; color:var(--ink); }
.item:hover,.item[aria-pressed=true] { background:#e8eee6; }
.code { font:700 12px ui-monospace,monospace; color:var(--accent); overflow-wrap:anywhere; }
.item-name { overflow-wrap:anywhere; }
.item-dim { color:var(--muted); font-size:12px; text-align:right; font-variant-numeric:tabular-nums; }
.detail-head { display:flex; justify-content:space-between; gap:8px; align-items:center; margin-bottom:10px; }
.secondary { background:transparent; color:var(--accent); border:1px solid #c7d2c7; border-radius:7px; padding:4px 8px; font-size:13px; }
.secondary:disabled { opacity:.4; cursor:default; }
.render-links { display:flex; flex-wrap:wrap; gap:8px; margin:0 0 16px; }
.render-links:empty { display:none; }
.render-links a { background:#e6ece3; text-decoration:none; padding:7px 12px; }
.detail-empty,.detail-meta { color:var(--muted); }
.detail-meta { margin-top:4px; font-size:13px; }
.detail-name,.detail-note { overflow-wrap:anywhere; }
.dimensions { display:grid; grid-template-columns:repeat(auto-fit,minmax(96px,1fr)); gap:7px; margin-top:12px; }
.dimension { padding:7px 8px; background:#f0f3ed; border-radius:7px; min-width:0; overflow-wrap:anywhere; }
.dimension b { display:block; font-variant-numeric:tabular-nums; }
.dimension span { color:var(--muted); font-size:12px; }
.detail-note { margin-top:12px; }
.detail details { margin-top:12px; overflow-wrap:anywhere; }
.detail details summary { cursor:pointer; color:var(--accent); }
.needs { margin-top:15px; }
.needs-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,230px),1fr)); gap:9px; margin-top:11px; }
.need { padding:10px; border-radius:8px; background:#f0f3ed; }
.need p { color:var(--muted); margin:4px 0; overflow-wrap:anywhere; }
.need .secondary { margin:5px 4px 0 0; }
.need details { margin-top:5px; color:var(--muted); font-size:13px; }
.need details summary { cursor:pointer; color:var(--accent); }
.need details ul { margin:5px 0; padding-left:18px; }
.service-decisions { margin-top:12px; color:var(--muted); }
.service-decisions summary { cursor:pointer; color:var(--accent); }
.service-decisions ul { padding-left:18px; }
.service-decisions li { margin:6px 0; overflow-wrap:anywhere; }
.pattern-list { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,280px),1fr)); gap:10px; margin-top:11px; }
.pattern { min-width:0; padding:11px; background:#f0f3ed; border:1px solid #d6ddd3; border-radius:9px; overflow-wrap:anywhere; }
.pattern.selected-pattern { border-color:#a7552d; box-shadow:0 0 0 1px #a7552d38; }
.pattern.in-detail { margin-top:12px; }
.pattern p { margin-top:5px; }
.pattern-frame { min-height:64px; margin-top:8px; border-radius:6px; padding:9px; display:grid; place-items:center; background:#e4eae2; }
.pattern-image { display:block; width:100%; max-height:190px; object-fit:contain; }
.pattern.in-detail .pattern-image { max-height:150px; }
.pattern-fit { font-variant-numeric:tabular-nums; color:var(--ink); font-size:13px; }
.pattern .secondary { margin-top:9px; }
.source { margin-top:14px; font-size:12px; color:var(--muted); overflow-wrap:anywhere; }
@media(max-width:970px) { .views,.workspace { grid-template-columns:1fr; } .viewport { height:clamp(265px,57vw,460px); } }
@media(max-width:600px) { main { padding:15px; } header { align-items:start; flex-direction:column; gap:13px; margin-bottom:17px; } .metrics { width:100%; } .metric { flex:1; min-width:0; } .view,.index,.detail,.needs { padding:12px; } .item { grid-template-columns:58px minmax(0,1fr); } .item-dim { grid-column:2; text-align:left; } }
</style>
</head>
<body><main>
<header><div class="heading"><p class="label">整区总览</p><h1 id="title"></h1><p class="description" id="summary"></p></div><div class="metrics" id="metrics"></div></header>
<nav class="render-links" id="render-links" aria-label="贴图效果"></nav>
<section class="views" aria-label="平面与三维总览">
  <article class="card view"><div class="view-head"><h2>分区平面</h2><p id="plan-note">点选构件查看尺寸</p></div><canvas class="viewport" id="plan" aria-label="分区平面。可在下方索引选中同一构件。"></canvas><div id="legend" class="legend"></div></article>
  <article class="card view"><div class="view-head"><h2>整区三维</h2><p>剖开近侧墙体，保留布局坐标</p></div><canvas class="viewport" id="axon" aria-label="整区三维剖切视图。可在下方索引选中同一构件。"></canvas><div class="legend"><span class="legend-item"><span class="swatch" style="background:#ad6039"></span>选中位置</span></div></article>
</section>
<section class="workspace" aria-label="构件定位与尺寸">
  <article class="card index"><div class="index-head"><h2>构件索引</h2><input class="search" id="search" type="search" placeholder="按名称、编号或尺寸查找" aria-label="查找构件"></div><p class="index-note" id="count"></p><div class="results" id="results"></div></article>
  <article class="card detail"><div class="detail-head"><h2>尺寸与位置</h2><button class="secondary" id="clear" type="button" disabled>取消高亮</button></div><div id="detail" aria-live="polite"></div></article>
</section>
<section id="services" class="card needs" hidden><h2>办公配套</h2><p class="index-note" id="service-basis"></p><div class="needs-grid" id="service-list"></div><details class="service-decisions" id="service-decisions" hidden><summary>查看设施取舍</summary><ul id="decision-list"></ul></details></section>
<section id="needs" class="card needs" hidden><h2>使用需求与落点</h2><div class="needs-grid" id="needs-list"></div></section>
<section id="selections" class="card needs" hidden><h2>材料与物件怎么选</h2><div class="needs-grid" id="selection-list"></div></section>
<section id="patterns" class="card needs" hidden><h2>饰面图案</h2><p class="index-note">按已校核构件的毫米范围排版。</p><div class="pattern-list" id="pattern-list"></div></section>
<p class="source" id="source"></p>
</main><script type="application/json" id="overview-data">__DATA__</script>
<script>__SCRIPT__</script></body></html>
"""


SCRIPT = r"""
"use strict";
const data = JSON.parse(document.getElementById("overview-data").textContent);
const get = id => document.getElementById(id);
const categories = {furniture:"家具",interior:"室内构件",fixed:"固定设施",opening:"门窗",void:"洞口"};
const dimensionLabels = {width_mm:"宽",depth_mm:"深",height_mm:"高",span_mm:"长度",length_mm:"长度",x_mm:"X",y_mm:"Y",z_mm:"Z"};
const palette = __ZONE_COLORS__;
const items = data.items || [];
const byId = new Map(items.map((item,index) => [item.id,{item,index}]));
const guide = data.positioning;
const positions = new Map((guide?.rows || []).map(row=>[row.ref,row]));
const selections = data.selections || [];
const patterns = (data.patterns || []).filter(row=>byId.has(row.ref));
const patternCards = [];
const zones = data.zones || [];
const plan = get("plan"), axon = get("axon");
const outer = data.outline.outer;
const holes = data.outline.holes || [];
const minX = Math.min(...outer.map(p => p[0])), maxX = Math.max(...outer.map(p => p[0]));
const minY = Math.min(...outer.map(p => p[1])), maxY = Math.max(...outer.map(p => p[1]));
const number = new Intl.NumberFormat("zh-CN",{maximumFractionDigits:2});
const positionNumber = new Intl.NumberFormat("zh-CN",{maximumFractionDigits:3});
const patternNumber = new Intl.NumberFormat("zh-CN",{maximumFractionDigits:6});
const pngURI = /^data:image\/png;base64,[A-Za-z0-9+/]+={0,2}$/;
let selected = null, planHits = [], axonCache = null;

function element(tag, className, value) {
  const node = document.createElement(tag);
  if(className) node.className = className;
  if(value !== undefined && value !== null) node.textContent = String(value);
  return node;
}
function detailDimensions(item) {
  const found = Object.entries(item.dimensions_mm || {}).filter(([,value]) => Number.isFinite(value))
    .map(([key,value]) => [dimensionLabels[key] || key, value]);
  if(Number.isFinite(item.bottom_mm)) found.push(["底标高",item.bottom_mm]);
  if(Number.isFinite(item.top_mm)) found.push(["顶标高",item.top_mm]);
  return found;
}
function concise(item) {
  return Object.entries(item.dimensions_mm || {}).filter(([,value]) => Number.isFinite(value))
    .map(([,value]) => number.format(value)).join(" × ");
}
function patternPair(values) {
  return Array.isArray(values) && values.length===2 && values.every(v=>Number.isFinite(v) && v>0);
}
function pairLabel(values) {
  return values.map(value=>patternNumber.format(value)).join(" × ")+" mm";
}
function axisFit(extent,repeat) {
  const ideal=extent/repeat, nearest=Math.round(ideal);
  if(!Number.isFinite(ideal)) return "";
  const exact=Math.abs(extent-nearest*repeat)<1e-7;
  const full=exact ? nearest : Math.floor(ideal);
  const tail=exact ? 0 : extent-full*repeat;
  return "整 "+patternNumber.format(full)+" 模"+(tail>0 ? " ＋ 余边 "+patternNumber.format(tail)+" mm" : "");
}
function patternBlock(pattern, item, inDetail=false) {
  const card=element("article","pattern"+(inDetail?" in-detail":""));
  card.append(element("h3",null,pattern.name));
  const floor=item.kind==="floor_finish", ceiling=["ceiling_baffle","ceiling_light"].includes(item.kind);
  const view=floor ? "地面平面" : ceiling ? "顶面展开" : "表面展开";
  const caption=inDetail ? "同坐标表面排版预览" : item.id+" · "+item.name+" · "+view;
  card.append(element("p","detail-meta",caption));
  const repeat=patternPair(pattern.repeat_mm), extent=patternPair(pattern.extent_mm);
  if(repeat) card.append(element("p","pattern-fit",(pattern.mode==="single"?"整幅尺寸 ":"重复单元 ")+pairLabel(pattern.repeat_mm)));
  if(extent) card.append(element("p","pattern-fit",(floor?"平面外接范围 ":"表面范围 ")+pairLabel(pattern.extent_mm)));
  if(repeat && extent && pattern.mode!=="single") {
    const horizontal=axisFit(pattern.extent_mm[0],pattern.repeat_mm[0]);
    const vertical=axisFit(pattern.extent_mm[1],pattern.repeat_mm[1]);
    if(horizontal && vertical) {
      card.append(element("p","detail-meta",(floor?"外接范围排布 · ":"")+"横向 "+horizontal+" · 纵向 "+vertical));
    }
  }
  if(pattern.status==="ready" && typeof pattern.preview_data_uri==="string" && pngURI.test(pattern.preview_data_uri)) {
    const frame=element("div","pattern-frame"), image=element("img","pattern-image");
    image.alt=pattern.name+" · "+view+"排版预览";
    image.decoding="async";
    if(!inDetail) image.loading="lazy";
    image.src=pattern.preview_data_uri;
    frame.append(image);card.append(frame);
  } else {
    card.append(element("p","detail-meta","图案预览待确认"));
  }
  if(pattern.studio_views?.length) card.append(element("p","detail-meta","三维已应用 · "+pattern.studio_views.join("、")));
  if(pattern.local_view) card.append(element("p","detail-meta","已应用到本地贴图三维"));
  if(pattern.note) card.append(element("p","detail-meta",pattern.note));
  return card;
}
function showPatterns() {
  if(!patterns.length) return;
  get("patterns").hidden=false;
  for(const pattern of patterns) {
    const item=byId.get(pattern.ref).item, card=patternBlock(pattern,item);
    const button=element("button","secondary","查看构件");
    button.type="button";button.setAttribute("aria-label","查看 "+item.name);
    button.setAttribute("aria-pressed","false");
    button.addEventListener("click",()=>{
      select(item.id);get("detail").scrollIntoView({block:"nearest"});
    });
    card.append(button);
    patternCards.push({id:item.id,card,button});
    get("pattern-list").append(card);
  }
}
function metric(value,label) {
  const box = element("div","metric");
  box.append(element("b",null,value),element("span",null,label));
  get("metrics").append(box);
}
get("title").textContent = data.title || "空间方案";
get("summary").textContent = typeof data.summary === "string" ? data.summary : "";
if(Number.isFinite(data.area_m2)) metric(number.format(data.area_m2)+" m²","净楼板面积");
if(Number.isFinite(data.aisle_mm)) metric(number.format(data.aisle_mm)+" mm","项目通行净宽");
if(data.source) get("source").textContent = "几何来源 · "+data.source;
zones.forEach((zone,i) => {
  const row=element("span","legend-item"), swatch=element("span","swatch");
  swatch.style.background=palette[i % palette.length];
  row.append(swatch,element("span",null,zone.name));
  if(zone.note) row.title=zone.note;
  get("legend").append(row);
});
if(holes.length) {
  const row=element("span","legend-item"), swatch=element("span","swatch");
  swatch.style.background="#d5dcd9";
  row.append(swatch,element("span",null,"楼板洞口"));
  get("legend").append(row);
}

function polygon(points) {
  const path=new Path2D();
  trace(path,points);
  return path;
}
function trace(path,points) {
  if(!points.length) return;
  path.moveTo(...points[0]);
  points.slice(1).forEach(p=>path.lineTo(...p));
  path.closePath();
}
function bounds(points) {
  return [Math.min(...points.map(p=>p[0])),Math.min(...points.map(p=>p[1])),
          Math.max(...points.map(p=>p[0])),Math.max(...points.map(p=>p[1]))];
}
function inside(x,y,points) {
  let hit=false;
  for(let i=0,j=points.length-1;i<points.length;j=i++) {
    const [a,b]=points[i], [c,d]=points[j];
    if((b>y)!==(d>y) && x<(c-a)*(y-b)/(d-b)+a) hit=!hit;
  }
  return hit;
}
function segmentDistance(x,y,points) {
  const [[a,b],[c,d]]=points, dx=c-a,dy=d-b;
  const t=Math.max(0,Math.min(1,((x-a)*dx+(y-b)*dy)/(dx*dx+dy*dy || 1)));
  return Math.hypot(x-a-dx*t,y-b-dy*t);
}
function outlineLine(ctx,points,color,width) {
  ctx.beginPath();ctx.moveTo(...points[0]);ctx.lineTo(...points[1]);
  ctx.strokeStyle=color;ctx.lineWidth=width;ctx.stroke();
}
function canvasSize(canvas) {
  const rect=canvas.getBoundingClientRect();
  const dpr=Math.min(1.65,window.devicePixelRatio || 1);
  const w=Math.max(1,Math.round(rect.width*dpr)), h=Math.max(1,Math.round(rect.height*dpr));
  if(canvas.width!==w || canvas.height!==h) {canvas.width=w;canvas.height=h;}
  return {w,h,dpr,cw:w/dpr,ch:h/dpr};
}
const order = {void:0,fixed:2,furniture:3,interior:4,opening:5};
const styles = {fixed:["#a3ae9e","#5f7061"],furniture:["#b9956f","#746452"],
                interior:["#a1aabc","#646f81"],void:["#dce3e0","#687c76"],opening:["#56877e","#56877e"]};
function layer(item) {return item.kind==="floor_finish" ? 1 : (order[item.category] ?? 3);}
const planItems = items.slice().sort((a,b)=>layer(a)-layer(b));
function drawZoneLabels(ctx,mapped,cw,ch) {
  ctx.font="650 12px system-ui, sans-serif";
  const reserved=[];
  for(const {zone,points,paint} of mapped) {
    const [x0,y0,x1,y1]=bounds(points);
    let caption=zone.name;
    while(caption.length>1 && ctx.measureText(caption).width>cw*.47) caption=caption.slice(0,-2)+"…";
    const half=ctx.measureText(caption).width/2+5, x=(x0+x1)/2, y=(y0+y1)/2;
    const slot=(cx,cy,insideZone)=>{
      const rect=[cx-half,cy-8,cx+half,cy+8], [a,b,c,d]=rect;
      if(a<3 || b<3 || c>cw-3 || d>ch-22) return null;
      if(reserved.some(([p,q,r,s])=>a<r+3 && c>p-3 && b<s+3 && d>q-3)) return null;
      if(insideZone && ![[a,b],[a,d],[c,b],[c,d]].every(([u,v])=>inside(u,v,points))) return null;
      return rect;
    };
    let rect=slot(x,y0+13,true)||slot(x,y1-13,true)||slot(x,y,true), leader=null;
    if(!rect) {
      let anchor=[x,y];
      if(!inside(...anchor,points)) {
        const steps=[.5,.25,.75,.12,.88];
        anchor=steps.flatMap(u=>steps.map(v=>[x0+(x1-x0)*u,y0+(y1-y0)*v]))
          .find(p=>inside(...p,points)) || points[0];
      }
      for(const radius of [23,37,56,77,104]) {
        for(const bearing of [6,2,7,5,1,3,0,4]) {
          const angle=bearing*Math.PI/4;
          rect=slot(anchor[0]+radius*Math.cos(angle),anchor[1]+radius*Math.sin(angle),false);
          if(rect) break;
        }
        if(rect) {leader=anchor;break;}
      }
    }
    if(!rect) continue;
    const [a,b,c,d]=rect;
    reserved.push(rect);
    if(leader) {
      const end=[Math.min(c,Math.max(a,leader[0])),Math.min(d,Math.max(b,leader[1]))];
      outlineLine(ctx,[leader,end],"#fffefa",3.5);outlineLine(ctx,[leader,end],paint,1.6);
    }
    ctx.fillStyle="#fffefa";ctx.fillRect(a,b,c-a,d-b);
    ctx.strokeStyle=paint;ctx.lineWidth=1;ctx.strokeRect(a,b,c-a,d-b);
    ctx.fillStyle="#35483f";ctx.textAlign="center";ctx.textBaseline="middle";ctx.fillText(caption,(a+c)/2,(b+d)/2);
  }
  ctx.textAlign="start";
}
function drawPlan() {
  const {cw,ch,dpr}=canvasSize(plan), ctx=plan.getContext("2d");
  ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,cw,ch);
  const pad=cw<450?16:25;
  const scale=Math.min((cw-2*pad)/(maxX-minX),(ch-2*pad-20)/(maxY-minY));
  const ox=(cw-(maxX-minX)*scale)/2, oy=(ch+(maxY-minY)*scale-16)/2;
  const map=([x,y])=>[ox+(x-minX)*scale,oy-(y-minY)*scale];
  const shell=polygon(outer.map(map));
  const floor=new Path2D(shell);
  holes.forEach(ring=>trace(floor,ring.map(map)));
  ctx.fillStyle="#fffdf5";ctx.fill(floor,"evenodd");
  const mapped=zones.map((zone,i)=>({zone,points:zone.polygon.map(map),paint:palette[i % palette.length]}));
  ctx.save();ctx.clip(floor,"evenodd");
  mapped.forEach(({points,paint})=>{
    const shape=polygon(points);
    ctx.fillStyle=paint+"51";ctx.fill(shape);ctx.strokeStyle=paint;ctx.lineWidth=1.6;ctx.stroke(shape);
  });
  ctx.restore();
  holes.forEach(ring=>{
    const shape=polygon(ring.map(map));
    ctx.fillStyle="#e3e9e5";ctx.fill(shape);
    ctx.save();ctx.clip(shape);ctx.strokeStyle="#9baea5";ctx.lineWidth=.8;
    ctx.beginPath();
    for(let x=-ch;x<cw;x+=8) {ctx.moveTo(x,ch);ctx.lineTo(x+ch,0);}
    ctx.stroke();ctx.restore();
    ctx.strokeStyle="#61776e";ctx.lineWidth=1.5;ctx.stroke(shape);
  });
  ctx.strokeStyle="#35483f";ctx.lineWidth=2.4;ctx.stroke(shell);
  planHits=[];
  for(const item of planItems) {
    const points=item.polygon?.length>=3 ? item.polygon.map(map) : null;
    const segment=item.segment?.length>=2 ? item.segment.slice(0,2).map(map) : null;
    if(!points && !segment) continue;
    const [fill,stroke]=styles[item.category] || styles.furniture;
    const on=selected===item.id;
    if(points && (item.category!=="void" || on)) {
      const shape=polygon(points), floorFinish=item.kind==="floor_finish";
      const overhead=["ceiling_light","ceiling_baffle"].includes(item.kind);
      ctx.fillStyle=on?"#cc86524b":floorFinish?"#ad947644":fill;
      ctx.strokeStyle=on?"#a7552d":stroke;
      ctx.lineWidth=on?3:1;
      if(overhead) ctx.setLineDash([4,3]); else ctx.fill(shape);
      ctx.stroke(shape);ctx.setLineDash([]);
    }
    if(segment) {
      outlineLine(ctx,segment,"#fffefa",on?8:6);
      outlineLine(ctx,segment,on?"#a7552d":stroke,on?4:3);
    }
    planHits.push({id:item.id,points,segment});
  }
  drawZoneLabels(ctx,mapped,cw,ch);
  if(selected) {
    const hit=planHits.find(row=>row.id===selected);
    if(hit?.points) {ctx.lineWidth=2.5;ctx.strokeStyle="#a7552d";ctx.stroke(polygon(hit.points));}
    if(hit?.segment) outlineLine(ctx,hit.segment,"#a7552d",4);
  }
  const front=positions.get(selected)?.front;
  if(front) {
    const line=front.segment.map(map);
    outlineLine(ctx,line,"#fffefa",7);outlineLine(ctx,line,"#a7552d",4);
  }
  if(guide) {
    const [a,b]=[guide.origin_mm,guide.reference_mm].map(map);
    ctx.setLineDash([5,4]);outlineLine(ctx,[a,b],"#31685c90",1.4);ctx.setLineDash([]);
    for(const [delta,label] of [[[36,0],"X"],[[0,-36],"Y"]]) {
      const end=[a[0]+delta[0],a[1]+delta[1]];
      outlineLine(ctx,[a,end],"#296356",2);
      ctx.font="700 11px system-ui, sans-serif";ctx.fillStyle="#296356";ctx.textBaseline="middle";
      ctx.fillText(label,end[0]+4,end[1]-5);
    }
    for(const [point,label] of [[a,"A"],[b,"B"]]) {
      ctx.beginPath();ctx.arc(...point,9,0,Math.PI*2);ctx.fillStyle="#296356";ctx.fill();
      ctx.fillStyle="#fffefa";ctx.font="750 11px system-ui, sans-serif";ctx.textAlign="center";
      ctx.textBaseline="middle";ctx.fillText(label,...point);
    }
    ctx.textAlign="start";
  }
  ctx.font="11px system-ui, sans-serif";ctx.textBaseline="top";
  const length=Math.min(cw*.18/scale,2000);
  const bar=length>=2000?2000:length>=1000?1000:length>=500?500:Math.max(1,Math.floor(length));
  ctx.fillStyle="#415a50";ctx.fillRect(pad,ch-20,bar*scale,2);
  ctx.fillText(number.format(bar/1000)+" m",pad,ch-16);
}

const a=(data.azimuth_degrees ?? data.axon_angle ?? 45)*Math.PI/180, e=42*Math.PI/180;
const ca=Math.cos(a),sa=Math.sin(a),ce=Math.cos(e),se=Math.sin(e);
const project=([x,y,z])=>[-sa*x+ca*y,-se*ca*x-se*sa*y+ce*z,ce*ca*x+ce*sa*y+se*z];
function faceColor(face) {
  const [p,q,r]=face.vertices;
  const u=q.map((v,i)=>v-p[i]),v=r.map((value,i)=>value-p[i]);
  const x=u[1]*v[2]-u[2]*v[1],y=u[2]*v[0]-u[0]*v[2],z=u[0]*v[1]-u[1]*v[0];
  const shade=.74+.24*Math.max(0,(-.214*x+.472*y+.855*z)/(Math.hypot(x,y,z)||1));
  return face.rgb.map(channel=>Math.min(255,Math.round(9+channel*shade)));
}
const faces=(data.faces || []).filter(face=>face.vertices?.length>=3).map(face=>({
  points:face.vertices.map(project),rgb:faceColor(face),index:byId.get(face.item)?.index ?? -1
}));
function rasterTriangle(a,b,c,w,h,depth,owners,pixels,face) {
  const left=Math.max(0,Math.floor(Math.min(a[0],b[0],c[0])));
  const right=Math.min(w-1,Math.ceil(Math.max(a[0],b[0],c[0])));
  const top=Math.max(0,Math.floor(Math.min(a[1],b[1],c[1])));
  const bottom=Math.min(h-1,Math.ceil(Math.max(a[1],b[1],c[1])));
  const denominator=(b[1]-c[1])*(a[0]-c[0])+(c[0]-b[0])*(a[1]-c[1]);
  if(Math.abs(denominator)<1e-7) return;
  const [red,green,blue]=face.rgb;
  for(let y=top;y<=bottom;y++) {
    for(let x=left;x<=right;x++) {
      const u=((b[1]-c[1])*(x+.5-c[0])+(c[0]-b[0])*(y+.5-c[1]))/denominator;
      if(u<-.00001 || u>1.00001) continue;
      const v=((c[1]-a[1])*(x+.5-c[0])+(a[0]-c[0])*(y+.5-c[1]))/denominator;
      const t=1-u-v;
      if(v<-.00001 || t<-.00001) continue;
      const z=u*a[2]+v*b[2]+t*c[2], i=y*w+x;
      if(z<=depth[i]) continue;
      depth[i]=z;owners[i]=face.index;
      const pixel=i*4;
      pixels[pixel]=red;pixels[pixel+1]=green;pixels[pixel+2]=blue;pixels[pixel+3]=255;
    }
  }
}
function buildAxon(w,h) {
  if(!faces.length) return null;
  let x0=Infinity,y0=Infinity,x1=-Infinity,y1=-Infinity;
  for(const face of faces) for(const [x,y] of face.points) {
    x0=Math.min(x0,x);y0=Math.min(y0,y);x1=Math.max(x1,x);y1=Math.max(y1,y);
  }
  const scale=Math.min(w*.92/Math.max(1,x1-x0),h*.88/Math.max(1,y1-y0));
  const offsetX=w/2-(x1+x0)*scale/2,offsetY=h/2+(y1+y0)*scale/2;
  const map=([x,y,z])=>[offsetX+x*scale,offsetY-y*scale,z];
  const depth=new Float32Array(w*h),owners=new Int32Array(w*h),pixels=new Uint8ClampedArray(w*h*4);
  depth.fill(-Infinity);owners.fill(-1);
  for(const face of faces) {
    const points=face.points.map(map);
    for(let i=1;i<points.length-1;i++) rasterTriangle(points[0],points[i],points[i+1],w,h,depth,owners,pixels,face);
  }
  return {w,h,owners,pixels,map};
}
function drawAxon() {
  const {w,h}=canvasSize(axon),ctx=axon.getContext("2d");
  if(!axonCache || axonCache.w!==w || axonCache.h!==h) axonCache=buildAxon(w,h);
  if(!axonCache) {
    ctx.clearRect(0,0,w,h);ctx.fillStyle="#62726a";ctx.font="16px sans-serif";
    ctx.textAlign="center";ctx.fillText("当前方案没有三维面数据",w/2,h/2);
    return;
  }
  const {owners,pixels,map}=axonCache, ref=byId.get(selected);
  const next=ref ? pixels.slice() : pixels;
  let visible=false;
  if(ref) for(let i=0;i<owners.length;i++) {
    if(owners[i]!==ref.index) continue;
    visible=true;
    const edge=i%w===0 || i%w===w-1 || owners[i-1]!==ref.index || owners[i+1]!==ref.index
      || owners[i-w]!==ref.index || owners[i+w]!==ref.index;
    const p=4*i, mix=edge ? .85 : .55;
    next[p]=pixels[p]*(1-mix)+190*mix;
    next[p+1]=pixels[p+1]*(1-mix)+101*mix;
    next[p+2]=pixels[p+2]*(1-mix)+51*mix;
  }
  ctx.putImageData(new ImageData(next,w,h),0,0);
  if(ref && !visible && ref.item.kind==="floor_finish" && ref.item.polygon?.length>=3) {
    const points=ref.item.polygon.map(([x,y])=>map(project([x,y,0])).slice(0,2));
    ctx.setLineDash([7,5]);ctx.strokeStyle="#a7552d";ctx.lineWidth=2.5;ctx.stroke(polygon(points));ctx.setLineDash([]);
  }
}

function showIndex() {
  const query=get("search").value.trim().normalize("NFKC").toLowerCase();
  const terms=query.split(/\s+/).filter(Boolean);
  const rows=items.filter(item=>{
    const haystack=[item.name,item.id,item.kind,categories[item.category],item.state,item.zone,
                   ...detailDimensions(item).flat(),
                   ...selections.filter(choice=>choice.refs.includes(item.id)).flatMap(choice=>[choice.name,choice.spec]),
                   ...patterns.filter(pattern=>pattern.ref===item.id).map(pattern=>pattern.name)]
                   .join(" ").normalize("NFKC").toLowerCase();
    return terms.every(term=>haystack.includes(term));
  });
  const list=get("results");
  const restore=list.scrollTop, fragment=document.createDocumentFragment();
  for(const item of rows) {
    const button=element("button","item");
    button.type="button";button.setAttribute("aria-pressed",String(selected===item.id));
    button.dataset.id=item.id;
    button.append(element("span","code",item.id),element("span","item-name",item.name),
                  element("span","item-dim",concise(item) ? concise(item)+" mm" : categories[item.category] || ""));
    button.addEventListener("click",()=>select(item.id,true));fragment.append(button);
  }
  list.replaceChildren(fragment);list.scrollTop=restore;
  get("count").textContent=query ? "找到 "+rows.length+" 项，共 "+items.length+" 项" : "共 "+items.length+" 项";
  if(!rows.length) list.append(element("p","detail-meta","没有找到匹配的构件。"));
}
function showDetail() {
  const detail=get("detail"), item=byId.get(selected)?.item;
  get("clear").disabled=!item;detail.replaceChildren();
  get("plan-note").textContent=item ? [item.name,concise(item) ? concise(item)+" mm" : ""].filter(Boolean).join(" · ")
                                   : "点选构件查看尺寸";
  if(!item) {detail.append(element("p","detail-empty","点选平面、三维构件或索引中的一项。"));return;}
  detail.append(element("h3","detail-name",item.name),
                element("p","detail-meta",[item.id,categories[item.category],item.state,item.zone].filter(Boolean).join(" · ")));
  const entries=detailDimensions(item);
  if(entries.length) {
    const values=element("div","dimensions");
    for(const [label,value] of entries) {
      const card=element("div","dimension");
      card.append(element("b",null,number.format(value)+" mm"),element("span",null,label));values.append(card);
    }
    detail.append(values);
  }
  if(item.note) detail.append(element("p","detail-note",item.note));
  const placement=positions.get(item.id);
  if(placement) {
    const points=element("details");
    points.append(element("summary",null,"从图上 A 点量取"));
    const title=item.segment?"线段两端":"轮廓点";
    const values=placement.points_mm.map(p=>"("+p.map(n=>positionNumber.format(n)).join(", ")+")").join("  /  ");
    points.append(element("p","detail-meta","X 向右，Y 向上，单位 mm。"+title+" "+values));
    if(placement.front) points.append(element("p","detail-meta","平面橙线为"+placement.front.label+"。"));
    detail.append(points);
  } else if(item.position_mm) {
    const position=item.position_mm;
    const angle=Number.isFinite(item.angle_deg) ? " · 旋转 "+number.format(item.angle_deg)+"°" : "";
    detail.append(element("p","detail-note",position.basis+"距底图左、下外接边界 "+
                  number.format(position.x_mm)+" / "+number.format(position.y_mm)+" mm"+angle));
  }
  if(item.color && /^#[0-9a-f]{6}$/i.test(item.color)) {
    const line=element("p","detail-note");
    const swatch=element("span","swatch");
    swatch.style.background=item.color;
    line.append(swatch, document.createTextNode(" 方案色 "+item.color));detail.append(line);
  }
  for(const pattern of patterns.filter(row=>row.ref===item.id)) detail.append(patternBlock(pattern,item,true));
  for(const choice of selections.filter(row=>row.refs.includes(item.id))) {
    detail.append(element("p","detail-note",choice.name+" · "+choice.spec));
    if(choice.note)detail.append(element("p","detail-meta",choice.note));
  }
}
function select(id,keepFocus=false) {
  selected=byId.has(id)?id:null;
  showIndex();showDetail();drawPlan();drawAxon();
  for(const {id,card,button} of patternCards) {
    card.classList.toggle("selected-pattern",selected===id);
    button.setAttribute("aria-pressed",String(selected===id));
  }
  if(keepFocus) [...get("results").querySelectorAll("button")].find(node=>node.dataset.id===selected)?.focus({preventScroll:true});
}
function pointer(canvas,event) {
  const rect=canvas.getBoundingClientRect();
  return [event.clientX-rect.left,event.clientY-rect.top,rect];
}
function hitPlan(event) {
  const [x,y]=pointer(plan,event);
  for(let i=planHits.length-1;i>=0;i--) {
    const {id,points,segment}=planHits[i];
    if((segment && segmentDistance(x,y,segment)<=7) || (points && inside(x,y,points))) return id;
  }
  return null;
}
function hitAxon(event) {
  if(!axonCache) return null;
  const [x,y,rect]=pointer(axon,event), {w,h,owners}=axonCache;
  const px=Math.floor(x*w/rect.width),py=Math.floor(y*h/rect.height);
  if(px<0 || py<0 || px>=w || py>=h) return null;
  return items[owners[py*w+px]]?.id ?? null;
}
for(const [canvas,hit] of [[plan,hitPlan],[axon,hitAxon]]) {
  canvas.addEventListener("click",event=>{const id=hit(event);if(id!==null)select(id);});
  canvas.addEventListener("pointermove",event=>{canvas.style.cursor=hit(event)!==null?"pointer":"default";});
}
get("clear").addEventListener("click",()=>select(null));
get("search").addEventListener("input",showIndex);
function showCards(rows, section, container) {
  if(!rows?.length)return;
  get(section).hidden=false;
  for(const need of rows) {
    const card=element("article","need");
    card.append(element("h3",null,need.name));
    if(need.status)card.append(element("p",null,need.status));
    if(need.target>0)card.append(element("p",null,"图中 "+need.refs.length+" 处 · 建议 "+need.target+" 处"));
    if(need.spec)card.append(element("p",null,need.spec));
    if(need.note)card.append(element("p",null,need.note));
    if(need.checks?.length) {
      const list=element("ul"), details=element("details");
      details.append(element("summary",null,"现场核对"));
      for(const check of need.checks)list.append(element("li",null,check));
      details.append(list);card.append(details);
    }
    for(const id of need.refs || []) {
      const item=byId.get(id)?.item;
      if(!item) continue;
      const zone=need.locations?.find(location=>location.ref===id)?.zone;
      const button=element("button","secondary",zone?item.name+" · "+zone:item.name);
      button.type="button";button.addEventListener("click",()=>{
        select(id);get("detail").scrollIntoView({block:"nearest"});
      });
      card.append(button);
    }
    get(container).append(card);
  }
}
showCards(data.needs,"needs","needs-list");
if(data.office_services) {
  const {people,area_m2,rows,decisions,unmapped_uses}=data.office_services;
  get("service-basis").textContent=number.format(area_m2)+" m² · "+(people>0?"按峰值 "+people+" 人":"峰值人数待确认");
  showCards(rows,"services","service-list");
  if(decisions?.length) {
    const choices={add:"建议新增",reuse:"共用",combine:"建议合并布置",omit:"省略",hold:"待核",keep:"保留本方案布置"};
    get("service-decisions").hidden=false;
    for(const entry of decisions) {
      const item=element("li");
      item.append(element("strong",null,entry.name+" · "+choices[entry.decision])," "+entry.reason);
      get("decision-list").append(item);
    }
    if(unmapped_uses?.length) {
      get("decision-list").append(element("li",null,"继续细化的用途 · "+unmapped_uses.join("、")));
    }
  }
}
showCards(selections,"selections","selection-list");
const studio = data.studio?.images?.[0];
if(guide) {
  const legend=element("span","legend-item","A、B 为底图核对点 · 间距 "+positionNumber.format(guide.reference_distance_mm)+" mm");
  get("legend").append(legend);
  for(const [file,title] of [["逐件摆放.csv","逐件摆放表"],["平面图.dxf","毫米 DXF"],["平面图.png","尺寸平面"]]) {
    const link=element("a","secondary",title);link.href=file;get("render-links").append(link);
  }
}
if(data.detail_views?.length) {
  const link=element("a","secondary","看配套局部");link.href="配套局部.png";get("render-links").append(link);
}
if(data.local_materials?.file==="本地贴图三维.png") {
  const link=element("a","secondary","看本地贴图三维");link.href=data.local_materials.file;get("render-links").append(link);
}
for(const [file, caption] of [["纹样三维.png","看整区贴图三维"],["墙地顶人视.png","看墙地顶人视"]]) {
  if(studio && [studio.file, studio.eye_file].includes(file)) {
    const link=element("a","secondary",caption);link.href=file;get("render-links").append(link);
  }
}
showPatterns();
showIndex();showDetail();drawPlan();drawAxon();
if(typeof ResizeObserver!=="undefined") {
  const observer=new ResizeObserver(()=>{drawPlan();drawAxon();});
  observer.observe(plan);observer.observe(axon);
} else window.addEventListener("resize",()=>{drawPlan();drawAxon();});
"""


def png_preview_uri(value):
    if not isinstance(value, str) or not value.startswith(PNG_DATA_PREFIX):
        return None
    try:
        binary = base64.b64decode(value[len(PNG_DATA_PREFIX):], validate=True)
        if not binary.startswith(b"\x89PNG\r\n\x1a\n"):
            return None
        with Image.open(BytesIO(binary)) as image:
            if image.format != "PNG":
                return None
            image.verify()
    except (binascii.Error, OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        return None
    return value


def overview_html(data):
    if "patterns" in data:
        rows = data["patterns"]
        patterns = []
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                preview = png_preview_uri(row.get("preview_data_uri")) if row.get("status") == "ready" else None
                patterns.append({**row, "preview_data_uri": preview, "status": "ready" if preview else "pending"})
        data = {**data, "patterns": patterns}
    payload = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")).replace("<", "\\u003c")
    values = {
        "TITLE": html.escape(str(data["title"])),
        "DATA": payload,
        "SCRIPT": SCRIPT.replace("__ZONE_COLORS__", json.dumps(ZONE_COLORS)),
    }
    return re.sub(r"__(TITLE|DATA|SCRIPT)__", lambda match: values[match[1]], SHELL)


def paint_plan_zones(image, data):
    if image.size != (1560, 1100) or not data["zones"]:
        return image
    outside = data["outline"]["outer"]
    shape = Polygon(outside, data["outline"]["holes"])
    x0, y0, x1, y1 = shape.bounds
    scale = min(1220 / (x1 - x0), 655 / (y1 - y0))
    left = 780 - (x1 - x0) * scale / 2
    bottom = 200 + (655 + (y1 - y0) * scale) / 2

    def screen(points):
        return [(left + (x - x0) * scale, bottom - (y - y0) * scale) for x, y in points]

    mask = Image.new("1", image.size)
    clip = ImageDraw.Draw(mask)
    clip.polygon(screen(outside), fill=1)
    for hole in data["outline"]["holes"]:
        clip.polygon(screen(hole), fill=0)
    tinted = image.copy()
    draw = ImageDraw.Draw(tinted, "RGBA")
    for index, zone in enumerate(data["zones"]):
        color = ZONE_COLORS[index % len(ZONE_COLORS)]
        red, green, blue = (int(color[pos:pos + 2], 16) for pos in (1, 3, 5))
        draw.polygon(screen(zone["polygon"]), fill=(red, green, blue, 44),
                     outline=(red, green, blue, 220), width=3)
    image.paste(tinted, (0, 0), mask)
    paint_zone_names(image, data, screen)
    return image


def paint_zone_names(image, data, screen):
    draw = ImageDraw.Draw(image)
    face = font(24, True)
    occupied = []
    positions = []
    bodies = union_all([Polygon(screen(item["polygon"])) for item in data["items"]
                        if item.get("polygon") and item["category"] in ("furniture", "fixed")])

    def slot(x, y, width):
        rect = box(x - width / 2, y - 15, x + width / 2, y + 15)
        a, b, c, d = rect.bounds
        if a < 65 or b < 172 or c > 1495 or d > 899:
            return None
        if any(rect.intersects(previous) for previous in occupied):
            return None
        return rect

    for index, zone in enumerate(data["zones"]):
        shape = Polygon(screen(zone["polygon"]))
        anchor = shape.representative_point()
        caption = shorten(draw, zone["name"], face, 270)
        width = draw.textlength(caption, font=face) + 22
        x0, y0, x1, y1 = shape.bounds
        center = (x0 + x1) / 2
        internal = ((center, y0 + 20), (center, y1 - 20), (anchor.x, anchor.y),
                    ((3 * x0 + x1) / 4, (y0 + y1) / 2), ((x0 + 3 * x1) / 4, (y0 + y1) / 2))
        candidates = [rect for x, y in internal if (rect := slot(x, y, width)) is not None and shape.covers(rect)]
        marker = None
        if candidates:
            rect = min(candidates, key=lambda candidate: candidate.intersection(bodies).area)
        else:
            nearby = []
            for radius in (37, 57, 79, 112, 154, 190):
                for index_angle in range(8):
                    angle = index_angle * pi / 4
                    candidate = slot(anchor.x + cos(angle) * radius, anchor.y + sin(angle) * radius, width)
                    if candidate is not None:
                        penalty = radius + candidate.intersection(bodies).area / 120
                        nearby.append((penalty, candidate))
            if not nearby:
                continue
            rect = min(nearby, key=lambda pair: pair[0])[1]
            marker = (anchor.x, anchor.y)
        occupied.append(rect.buffer(6))
        positions.append((rect.bounds, caption, ZONE_COLORS[index % len(ZONE_COLORS)], marker))
    for (x0, y0, x1, y1), _, paint, marker in positions:
        if marker:
            x, y = marker
            end = (max(x0, min(x, x1)), max(y0, min(y, y1)))
            draw.line([marker, end], fill="#fffefa", width=6)
            draw.line([marker, end], fill=paint, width=3)
            draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=paint, outline="#fffefa")
    for (x0, y0, x1, y1), caption, paint, _ in positions:
        draw.rounded_rectangle((x0, y0, x1, y1), radius=6, fill="#fffefa", outline=paint, width=2)
        draw.text(((x0 + x1) / 2, (y0 + y1) / 2), caption, font=face, anchor="mm", fill=INK)


def inset(canvas, content, box):
    x0, y0, x1, y1 = box
    fitted = ImageOps.contain(content, (x1 - x0, y1 - y0), Image.Resampling.LANCZOS)
    canvas.paste(fitted, (x0 + (x1 - x0 - fitted.width) // 2, y0 + (y1 - y0 - fitted.height) // 2))


def overview_image(data, plan_path, axon_path):
    with Image.open(plan_path) as source:
        plan = paint_plan_zones(source.convert("RGB"), data)
    with Image.open(axon_path) as source:
        axon = source.convert("RGB")
    if plan.size == (1560, 1100):
        plan = plan.crop((42, 154, 1518, 975))
    if axon.size == (1350, 975):
        axon = axon.crop((0, 146, 1350, 916))

    image = Image.new("RGB", (2160, 1136), PAPER)
    draw = ImageDraw.Draw(image)
    draw.text((64, 49), "整区总览", font=font(24, True), fill="#296356")
    title_font = font(60, True)
    draw.text((61, 88), shorten(draw, data["title"], title_font, 1490), font=title_font, fill=INK)
    if data.get("summary"):
        summary_font = font(23)
        draw.text((65, 167), shorten(draw, str(data["summary"]), summary_font, 1390), font=summary_font, fill=MUTED)
    for x, value, label in ((1586, f'{data["area_m2"]:g} m²', "净楼板面积"),
                            (1857, f'{data["aisle_mm"]:g} mm', "项目通行净宽")):
        draw.rounded_rectangle((x, 82, x + 238, 184), radius=13, fill="#e4eae2")
        draw.text((x + 18, 91), value, font=font(31, True), fill=INK)
        draw.text((x + 19, 141), label, font=font(19), fill=MUTED)

    cards = ((60, 231, 1116, 1025), (1134, 231, 2100, 1025))
    for card, title, artwork in zip(cards, ("分区平面", "整区三维"), (plan, axon), strict=True):
        left, top, right, bottom = card
        draw.rounded_rectangle(card, radius=19, fill="#fffefa", outline=STROKE, width=2)
        draw.text((left + 22, top + 17), title, font=font(29, True), fill=INK)
        inset(image, artwork, (left + 20, top + 63, right - 20, bottom - 109))

    x, y = 85, 928
    label_font = font(18)
    for index, zone in enumerate(data["zones"]):
        caption = shorten(draw, zone["name"], label_font, 210)
        width = round(draw.textlength(caption, font=label_font)) + 45
        if x + width > 1095:
            x, y = 85, y + 28
        if y > 984:
            draw.text((85, 1000), "更多分区见交互版", font=font(15), fill=MUTED)
            break
        draw.rounded_rectangle((x, y + 5, x + 15, y + 20), radius=4,
                               fill=ZONE_COLORS[index % len(ZONE_COLORS)])
        draw.text((x + 23, y), caption, font=label_font, fill=MUTED)
        x += width
    model_caption = ("贴图视图 · 物件尺寸可在 HTML 查阅。" if data.get("local_materials", {}).get("applied")
                     or data.get("studio", {}).get("images") else "打开 HTML 可点选平面或三维构件，查看对应尺寸。")
    draw.text((1160, 955), model_caption, font=font(20), fill=MUTED)
    if data.get("source"):
        source_font = font(17)
        text = shorten(draw, "几何来源 · " + str(data["source"]), source_font, 2020)
        draw.text((64, 1064), text, font=source_font, fill=MUTED)
    return image


def write_overview(root, data, plan_path, axon_path):
    """Write the interactive overview and its static preview beside the plan bundle."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    if any((root / name).exists() for name in (HTML_NAME, PNG_NAME)):
        raise InputError("整区总览目标已存在，请使用新的输出目录。")
    with TemporaryDirectory(prefix=".opc-overview-", dir=root) as temporary:
        stage = Path(temporary)
        (stage / HTML_NAME).write_text(overview_html(data), encoding="utf-8")
        overview_image(data, plan_path, axon_path).save(stage / PNG_NAME)
        for name in (HTML_NAME, PNG_NAME):
            (stage / name).replace(root / name)
    return {"html": HTML_NAME, "png": PNG_NAME}
