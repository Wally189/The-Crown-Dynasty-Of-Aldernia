import { chromium } from 'playwright';
import fs from 'node:fs';
fs.mkdirSync('qa-artifacts',{recursive:true});
const base='http://127.0.0.1:4173/';
const pages=['index.html','government.html','cabinet.html','institutions.html','services.html','economy.html','country.html','life.html','learn.html','archive.html','experiment-record.html','experiment.html','experiment-journal.html','legal.html','privacy.html','accessibility.html'];
const viewports=[['desktop',{width:1365,height:768}],['mobile',{width:360,height:800}],['reflow320',{width:320,height:800}]];
const failures=[]; const report=[];
function fail(page,v,msg){failures.push(page+' ['+v+'] '+msg)}
function rgb(s){const m=s.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);return m?[+m[1],+m[2],+m[3]]:null}
function lum(c){return c.map(v=>{v/=255;return v<=.04045?v/12.92:Math.pow((v+.055)/1.055,2.4)}).reduce((s,v,i)=>s+v*[.2126,.7152,.0722][i],0)}
function ratio(a,b){const x=lum(a),y=lum(b);return (Math.max(x,y)+.05)/(Math.min(x,y)+.05)}
const browser=await chromium.launch({headless:true});
for(const [vname,vp] of viewports){
 const ctx=await browser.newContext({viewport:vp});
 for(const path of pages){
  const page=await ctx.newPage(); const consoleErrors=[];
  page.on('console',m=>{if(m.type()==='error')consoleErrors.push(m.text())});
  const res=await page.goto(base+path,{waitUntil:'networkidle'});
  if(!res||!res.ok()) fail(path,vname,'HTTP '+(res?.status()??'NO RESPONSE'));
  const metrics=await page.evaluate(()=>{
    const d=document.documentElement,b=document.body,h1=document.querySelectorAll('h1');
    const scripts=document.querySelectorAll('script').length,forms=document.querySelectorAll('form').length,iframes=document.querySelectorAll('iframe').length;
    const overflow=[...document.querySelectorAll('body *')].map(el=>{const r=el.getBoundingClientRect();return {tag:el.tagName,cls:String(el.className||''),left:r.left,right:r.right,width:r.width}}).filter(r=>r.width>0&&(r.left<-1||r.right>d.clientWidth+1)).slice(0,8);
    return {clientWidth:d.clientWidth,scrollWidth:d.scrollWidth,h1:h1.length,main:!!document.querySelector('main'),title:document.title,scripts,forms,iframes,overflow,bodyBg:getComputedStyle(b).backgroundColor};
  });
  if(metrics.scrollWidth>metrics.clientWidth+1) fail(path,vname,'horizontal overflow '+metrics.scrollWidth+'/'+metrics.clientWidth);
  if(metrics.h1!==1) fail(path,vname,'H1 count '+metrics.h1);
  if(!metrics.main) fail(path,vname,'main landmark missing');
  if(metrics.scripts||metrics.forms||metrics.iframes) fail(path,vname,'active script/form/iframe present');
  if(consoleErrors.length) fail(path,vname,'console errors: '+consoleErrors.join(' | '));
  if(path==='index.html'){
    const first=await page.evaluate(()=>{const h1=document.querySelector('h1'),menu=document.querySelector('.mobile-menu'),cs=h1?getComputedStyle(h1):null;return {h1Top:h1?.getBoundingClientRect().top??9999,h1Font:cs?parseFloat(cs.fontSize):0,mobileMenuOpen:menu?.open??false}});
    if(vname!=='desktop'&&first.mobileMenuOpen) fail(path,vname,'mobile national menu starts open');
    if(vname!=='desktop'&&first.h1Top>330) fail(path,vname,'lead story begins too low '+first.h1Top);
    if(vname!=='desktop'&&first.h1Font>46) fail(path,vname,'mobile lead type too large '+first.h1Font);
    if(vname==='desktop'&&first.h1Top>280) fail(path,vname,'desktop lead starts too low '+first.h1Top);
  }
  if(path==='experiment.html'||path==='experiment-journal.html'){
    const em=await page.evaluate(()=>{const country=document.querySelector('.experiment-country-menu'),h1=document.querySelector('h1'),header=document.querySelector('.experiment-site-header');return {countryOpen:country?.open??null,h1Top:h1?.getBoundingClientRect().top??9999,headerBottom:header?.getBoundingClientRect().bottom??0}});
    if(vname!=='desktop'&&em.countryOpen) fail(path,vname,'country menu starts open');
    if(vname!=='desktop'&&em.h1Top>430) fail(path,vname,'narrative H1 pushed too low '+em.h1Top);
  }
  if(path==='index.html'&&vname==='mobile'){
    await page.keyboard.press('Tab'); await page.keyboard.press('Tab'); await page.keyboard.press('Tab');
    const focus=await page.evaluate(()=>{const a=document.activeElement,cs=getComputedStyle(a);return {tag:a?.tagName,name:a?.textContent?.trim().slice(0,80),outlineStyle:cs.outlineStyle,outlineWidth:cs.outlineWidth,outlineColor:cs.outlineColor}});
    if(!focus||focus.outlineStyle==='none'||parseFloat(focus.outlineWidth||'0')<2) fail(path,vname,'visible keyboard focus not evidenced '+JSON.stringify(focus));
    const contrasts=await page.evaluate(()=>[document.querySelector('.lead h1'),document.querySelector('.dek'),document.querySelector('.brief p')].filter(Boolean).map(el=>({fg:getComputedStyle(el).color,bg:getComputedStyle(document.body).backgroundColor})));
    for(const c of contrasts){const a=rgb(c.fg),b=rgb(c.bg);if(a&&b&&ratio(a,b)<4.5) fail(path,vname,'representative text contrast '+ratio(a,b).toFixed(2));}
  }
  await page.screenshot({path:'qa-artifacts/'+path.replace('.html','')+'-'+vname+'.png',fullPage:true});
  report.push({path,vname,title:metrics.title,clientWidth:metrics.clientWidth,scrollWidth:metrics.scrollWidth,bodyBg:metrics.bodyBg});
  await page.close();
 }
 await ctx.close();
}
await browser.close();
fs.writeFileSync('qa-artifacts/report.json',JSON.stringify({commit:process.env.GITHUB_SHA,report,failures},null,2));
console.log('EXACT_SHA='+process.env.GITHUB_SHA);
console.log('PAGES='+pages.length+' VIEWPORTS='+viewports.length+' CASES='+(pages.length*viewports.length));
console.log('FAILURES='+failures.length);
if(failures.length){for(const f of failures)console.error('FAIL '+f);process.exit(1)}
console.log('PASS: desktop, mobile and 320px reflow browser assurance complete.');
