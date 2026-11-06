/* Stable routes and contextual destinations must survive the navigation redesign. */
const fs = require('node:fs');
const assert = require('node:assert/strict');
const path = require('node:path');
const ts = require('../frontend/node_modules/typescript');
const source = fs.readFileSync(path.join(__dirname, '../frontend/src/navigationCatalog.ts'), 'utf8');
const exportsObject = {};
new Function('exports', ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText)(exportsObject);
const {NAVIGATION, APP_PAGES, PAGE_ALIASES, visibleNavigation, activeNavigation, navigationParams, PROCUREMENT_PAGES} = exportsObject;
assert.equal(NAVIGATION.length, 11);
const entities = NAVIGATION.find(g => g.id === 'entidades');
assert.equal(entities.label, 'Entidades');
assert.deepEqual(entities.items.map(i => i.id), ['comercial:Clientes', 'compras:Fornecedores']);
for (const page of ['Clientes', 'Fornecedores']) {
  assert.equal(activeNavigation(NAVIGATION, page, '').group.id, 'entidades');
  assert.equal(activeNavigation(NAVIGATION, page, new URLSearchParams({'menu.area': page === 'Clientes' ? 'comercial' : 'fornecedores'}).toString()).group.id, 'entidades');
}
const all = NAVIGATION.flatMap(g => g.items);
const reconciliation = all.find(i => i.id === 'financeiro:Reconciliação automática');
assert.equal(reconciliation.params['area.reconciliacao'], 'movimentos');
assert.equal(reconciliation.params['area.reconciliacao-modo'], 'grelha');
const targets = all.flatMap(i => [i, ...(i.relatedTargets || [])]);
assert.equal(new Set(all.map(i => i.id)).size, all.length);
assert.equal(new Set(targets.map(i => i.id)).size, targets.length);
for (const page of APP_PAGES) assert(targets.some(i => i.page === (PAGE_ALIASES[page] || page)), `Unreachable legacy destination: ${page}`);
const signatures = all.map(i => JSON.stringify([i.page, Object.entries(i.params || {}).sort()]));
assert.equal(new Set(signatures).size, signatures.length, 'Duplicate main destinations');
for (const role of ['administrador', 'gestor', 'operator', 'operador']) {
  for (const mode of ['local', 'demonstracao', 'wintouch']) {
    const groups = visibleNavigation(role, mode);
    for (const group of groups) for (const parent of group.items) for (const item of [parent, ...(parent.relatedTargets || [])]) {
      assert(APP_PAGES.includes(item.page), item.page);
      if (item.managerOnly) assert(['administrador','gestor'].includes(role));
      if (item.localOnly) assert.equal(mode, 'local');
      const query = new URLSearchParams(navigationParams(group,item)).toString();
      assert.equal(activeNavigation(groups,item.page,query).item.id,parent.id, `Wrong current location: ${item.id}`);
    }
  }
}
const orderGroups = visibleNavigation('administrador', 'local');
const orderGroup = orderGroups.find(g => g.items.some(i => i.params?.['area.pedidos-clientes'] === 'pedidos'));
const orderItem = orderGroup.items.find(i => i.params?.['area.pedidos-clientes'] === 'pedidos');
const detailParams = new URLSearchParams(navigationParams(orderGroup, orderItem));
detailParams.set('filtro.pedidos', 'todos');
assert.equal(activeNavigation(orderGroups, orderItem.page, detailParams.toString()).item.id, orderItem.id);
detailParams.set('area.pedidos-clientes', 'historico');
assert.notEqual(activeNavigation(orderGroups, orderItem.page, detailParams.toString()).item.id, orderItem.id);
assert(PROCUREMENT_PAGES.includes('Compras'));
assert.equal(PAGE_ALIASES['Receitas e despesas'], 'Mapas de gestão');
assert.equal(all.find(i => i.label==='Planeamento de compras').params['filtro.abastecimento'],'buy');
assert.deepEqual(NAVIGATION.find(g=>g.id==='encomendas').items.map(i=>i.label),['Visão geral','Pedidos de clientes','Planeamento de compras','Encomendas a fornecedores','Levantamentos e contactos','Histórico']);
assert.deepEqual(NAVIGATION.find(g=>g.id==='artigos').items.map(i=>i.label),['Catálogo de artigos','Lotes e validades']);
assert(!NAVIGATION.find(g=>g.id==='financeiro').items.some(i=>['Meios de pagamento','Prazos de pagamento'].includes(i.label)));
assert(NAVIGATION.find(g=>g.id==='admin').items.some(i=>i.label==='Meios de pagamento'));
console.log(`PASS: ${new Set(APP_PAGES).size} legacy pages, ${all.length} destinations, 12 role/mode combinations, 11 groups and contextual active states.`);
