() => {
  const rgb = v => { const n = v.match(/[\d.]+/g); if(!n) return [0,0,0,0]; const scale=v.startsWith('color(srgb ')?255:1; return [+n[0]*scale, +n[1]*scale, +n[2]*scale, n.length > 3 ? +n[3] : 1]; };
  const over = (a,b) => { const t=a[3]+b[3]*(1-a[3]); return t ? [0,1,2].map(i=>(a[i]*a[3]+b[i]*b[3]*(1-a[3]))/t).concat(t) : [0,0,0,0]; };
  const lum = c => c.slice(0,3).map(v=>v/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4).reduce((v,x,i)=>v+x*[.2126,.7152,.0722][i],0);
  const ratio = (a,b) => (Math.max(lum(a),lum(b))+.05)/(Math.min(lum(a),lum(b))+.05);
  const bg = el => { let color=[0,0,0,0]; for(let n=el;n && color[3]<.999;n=n.parentElement) color=over(color,rgb(getComputedStyle(n).backgroundColor)); return over(color,[255,255,255,1]); };
  const shown = el => { const s=getComputedStyle(el), r=el.getBoundingClientRect(); return r.width>0&&r.height>0&&s.visibility==='visible'&&!el.closest('[hidden],[inert]')&&!Array.from((function*(n){while(n){yield n;n=n.parentElement;}})(el)).some(n=>getComputedStyle(n).opacity==='0'); };
  const rows=[], skipped=[];
  for(const el of document.querySelectorAll('body *')) {
    if(!shown(el)||el.matches('script,style,svg,svg *,option')) continue;
    if(el.closest('dialog')&&!el.closest('dialog[open]')) continue;
    if(document.querySelector('dialog[open]')&&!el.closest('dialog[open]')) continue;
    const own=Array.from(el.childNodes).filter(n=>n.nodeType===3).map(n=>n.textContent.trim()).filter(Boolean).join(' ');
    const value=el.matches('input:not([type=checkbox],[type=radio],[type=hidden]),select,textarea') ? el.value : '';
    const text=own||value;
    if(!text&&!el.getAttribute('placeholder')) continue;
    if(el.matches(':disabled')||el.closest('button:disabled,fieldset:disabled')) { skipped.push(text.slice(0,80)); continue; }
    const s=getComputedStyle(el), back=bg(el), front=over(rgb(s.color),back), score=ratio(front,back);
    const size=parseFloat(s.fontSize), large=size>=24 || (size>=18.66 && +s.fontWeight>=700), min=large?3:4.5;
    rows.push({text:text.slice(0,100),tag:el.tagName,cls:el.className,foreground:s.color,background:back.slice(0,3).map(Math.round),size,ratio:+score.toFixed(2),minimum:min,pass:score+.001>=min});
    if(el.matches('input,textarea')&&el.getAttribute('placeholder')) {
      const p=getComputedStyle(el,'::placeholder'), pr=ratio(over(rgb(p.color),back),back);
      rows.push({text:'Placeholder: '+el.getAttribute('placeholder'),tag:el.tagName,cls:el.className,ratio:+pr.toFixed(2),minimum:4.5,pass:pr+.001>=4.5});
    }
  }
  const controls=[];
  for(const el of document.querySelectorAll('input:not([type=hidden]),select,textarea,button svg')) {
    if(!shown(el)||el.matches(':disabled')||el.closest('button:disabled,fieldset:disabled')||(document.querySelector('dialog[open]')&&!el.closest('dialog[open]'))) continue;
    const s=getComputedStyle(el), svg=el.tagName.toLowerCase()==='svg', back=svg?bg(el):bg(el.parentElement);
    if(svg && (el.parentElement.innerText||'').trim()) continue;
    if(!svg && el.matches('[type=checkbox],[type=radio]') && s.appearance!=='none') continue;
    const value=svg?s.color:s.borderTopColor, score=ratio(over(rgb(value),back),back);
    controls.push({label:el.getAttribute('aria-label')||el.closest('label')?.innerText?.trim()||el.parentElement.getAttribute('aria-label')||el.tagName,kind:svg?'icon':'border',ratio:+score.toFixed(2),pass:score+.001>=3});
    if(!svg && s.outlineStyle!=='none' && parseFloat(s.outlineWidth)>0) { const focus=ratio(rgb(s.outlineColor),back); controls.push({label:'Focus '+el.tagName,kind:'focus',ratio:+focus.toFixed(2),pass:focus+.001>=3}); }
    if(!svg && el.matches('[type=checkbox]:checked,[type=radio]:checked')) { const glyph=ratio([255,255,255,1],bg(el)); controls.push({label:'Selected '+el.tagName,kind:'selected',ratio:+glyph.toFixed(2),pass:glyph+.001>=3}); }
  }
  return {checked:rows.length,failures:rows.filter(r=>!r.pass),small:rows.filter(r=>r.size<12),minimum:Math.min(...rows.map(r=>r.ratio)),inactive:skipped.length,rows,controls,overflow:document.documentElement.scrollWidth>innerWidth};
}
