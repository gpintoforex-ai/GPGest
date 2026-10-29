const { chromium } = require(process.env.PLAYWRIGHT_NODE_PATH || 'playwright');
const fs = require('fs');
const assert = require('node:assert/strict');
const fixture = JSON.parse(fs.readFileSync(require('node:path').join(__dirname, 'fixtures/article-grid.json'), 'utf8'));
const output=require('node:path').join(__dirname,'../.local/stock-grid-ui'); fs.mkdirSync(output,{recursive:true});
(async () => {
 const browser = await chromium.launch({headless:true});
 const page = await browser.newPage({ viewport:{width:1440,height:1000},locale:'pt-PT' });
 const errors=[]; page.on('pageerror', e=>errors.push(e.message));
 const rows=Array.from({length:10000},(_,i)=>({...fixture.article,id:`a${i}`,code:`A${String(i).padStart(5,'0')}`,name:`Artigo fictício ${String(i).padStart(5,'0')}`,active:true,expiry_state:i%2?'near':'missing'}));
 let writable=true;
 const writes=[],queries=[];
 await page.route('**/api/**',async route=>{
   const req=route.request(),url=new URL(req.url()),q=Object.fromEntries(url.searchParams),path=url.pathname;
   let result=[];
   if(path==='/api/bootstrap') result=fixture.boot;
   else if(path==='/api/stock/catalog'){
     queries.push(q);let filtered=rows.filter(r=>(!q.q || [r.name,r.code,r.reference,...r.barcodes].join(' ').toLowerCase().includes(q.q.toLowerCase())) && (!q.active || String(r.active)===q.active));
     if(q.direction==='desc')filtered=filtered.toReversed();const size=Number(q.size||25),current=Number(q.page||1);
     result={...fixture.catalog,permissions:writable?fixture.catalog.permissions:{adjust_stock:false,view_cost:false,change_price:false},facets:{family:['Suplementos'],category:['Vitaminas'],brand:['Marca fictícia'],unit:['un']},preference_scope:'fictitious.large',total:filtered.length,total_all:rows.length,page:current,pages:Math.max(1,Math.ceil(filtered.length/size)),articles:q.metadata_only?[]:q.page?filtered.slice((current-1)*size,current*size):filtered};
   } else if(path.endsWith('/market-prices')&&req.method()==='GET')result={prices:[],summary:{stores:0,own_cents:1230},search_url:'https://www.kuantokusta.pt/search?q=A00000',can_edit:writable};
   else if(path.endsWith('/details'))result={company_name:'Empresa fictícia',history:[],warehouses:[],expiry_alerts:[]};
   else if(path==='/api/stock/report') result={rows:[],totals:{},coverage:{},limitations:[]};
   else if(req.method()!=='GET'){
      const body=req.postDataJSON();writes.push({path,body});
      if(path==='/api/stock/articles'){result={id:'new'};}
      else if(path.includes('/api/stock/articles/')){const row=rows.find(r=>path.endsWith('/'+r.id));if(row)Object.assign(row,body);result={id:row?.id};}
   }
   await route.fulfill({json:result});
 });
 await page.goto((process.env.GPGEST_UI_URL || 'http://127.0.0.1:5174') + '/?pagina=Artigos%20e%20validades');
 await page.getByRole('button',{name:'Artigos',exact:true}).click();
 const table=page.getByRole('table',{name:'Artigos',exact:true});await table.locator('tbody tr').first().waitFor();
 await page.waitForFunction(()=>document.querySelectorAll('.article-grid tbody tr').length===25);
 assert.equal(await table.locator('tbody tr').count(),25);
 await page.getByRole('button',{name:'Seguinte',exact:true}).click();
 await page.getByText('A00025',{exact:true}).waitFor();
 await page.getByRole('button',{name:'Consultar',exact:true}).first().click();
 let dialog=page.getByRole('dialog',{name:'Consultar artigo',exact:true});await dialog.waitFor();
 assert.equal(await dialog.getByRole('button',{name:'Guardar',exact:true}).count(),0);
 assert.ok(await dialog.getByLabel('Código interno',{exact:true}).getAttribute('readonly')!==null);
 await dialog.getByRole('tab',{name:'Histórico',exact:true}).click();
 await page.keyboard.press('Escape');await dialog.waitFor({state:'hidden'});
 const search=page.getByLabel('Pesquisar código, designação, barras ou referência do fornecedor');await search.fill('A00003');
 await page.waitForFunction(()=>document.querySelectorAll('.article-grid tbody tr').length===1);
 await page.getByLabel('Mais ações para Artigo fictício 00003',{exact:true}).click();
 await page.getByRole('button',{name:'Duplicar',exact:true}).click();
 dialog=page.getByRole('dialog',{name:'Novo artigo',exact:true});await dialog.waitFor();
 assert.equal(await dialog.getByLabel('Código interno',{exact:true}).inputValue(),'');
 assert.equal(await dialog.getByLabel('Códigos de barras (separados por |)',{exact:true}).inputValue(),'');
 await dialog.getByLabel('Código interno',{exact:true}).fill('COPIA');await dialog.getByRole('button',{name:'Guardar',exact:true}).click();await dialog.waitFor({state:'hidden'});
 assert.equal(writes.at(-1).body.code,'COPIA');assert.equal(writes.at(-1).body.erp_id,undefined);
 await page.getByLabel('Selecionar todos os artigos desta página').check();
 await page.getByRole('button',{name:'Desativar seleção',exact:true}).click();
 await page.getByRole('alertdialog').getByRole('button',{name:'Desativar',exact:true}).click();
 await page.waitForFunction(()=>document.querySelector('.article-grid tbody')?.textContent?.includes('Inativo'));
 await page.getByLabel('Densidade',{exact:true}).selectOption('compact');
 await page.getByText('Colunas',{exact:true}).click();await page.getByLabel('Mostrar coluna Validade',{exact:true}).check();
 const saved = await page.evaluate(()=>JSON.parse(localStorage.getItem('iazela.articles.fictitious.large.v1')));assert.equal(saved.density,'compact');assert.ok(!saved.hidden.includes('expiry'));
 await page.reload();await page.locator('.article-grid.density-compact tbody tr').first().waitFor();assert.equal(await table.locator('tbody tr').count(),25);
 await page.locator('.article-columns').evaluate(e=>e.open=false);
 await page.locator('.article-row-actions details[open]').evaluateAll(elements=>elements.forEach(e=>e.open=false));
 await page.screenshot({path:require('node:path').join(output,'articles-desktop.png'),fullPage:true});
 await page.evaluate(()=>document.documentElement.dataset.theme='dark');await page.screenshot({path:require('node:path').join(output,'articles-dark.png'),fullPage:true});
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:require('node:path').join(output,'articles-mobile.png'),fullPage:true});
 assert.ok(queries.some(q=>q.page==='2'));assert.ok(queries.some(q=>q.q==='A00003'));
 writable=false;await page.reload();await page.locator('.article-grid tbody tr').first().waitFor();assert.equal(await page.locator('.article-row-actions details').count(),0);assert.equal(await page.getByLabel('Mostrar coluna Custo médio / última compra',{exact:true}).count(),0);
 assert.deepEqual(errors,[]); console.log('PASS: 10 000 fictitious articles; 25 rows/page; next page; search; consultation; duplicate; bulk deactivation; preferences; desktop/mobile/dark screenshots.');
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
