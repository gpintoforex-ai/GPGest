/* Architectural accessibility checks; no browser or application data required. */
const fs = require("node:fs");
const path = require("node:path");
const ts = require("../frontend/node_modules/typescript");
const root = path.resolve(__dirname, "../frontend/src");
const issues = [];
const stats = { windows: 0, buttons: 0, forms: 0, notices: 0 };
function inspect(file) {
  const source = fs.readFileSync(file, "utf8");
  const ast = ts.createSourceFile(
    file,
    source,
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.TSX,
  );
  const report = (node, message) =>
    issues.push(
      `${path.relative(root, file)}:${ast.getLineAndCharacterOfPosition(node.getStart(ast)).line + 1}: ${message}`,
    );
  const attributes = (node) =>
    new Map(
      node.attributes.properties
        .filter(ts.isJsxAttribute)
        .map((p) => [p.name.text, p]),
    );
  function visit(node) {
    if (
      ts.isCallExpression(node) &&
      /^(window\.)?(alert|confirm)$/.test(node.expression.getText(ast))
    )
      report(node, "Use a confirmação partilhada, sem alertas nativos.");
    if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) {
      const tag = node.tagName.getText(ast),
        attrs = attributes(node);
      if (["dialog", "button", "form"].includes(tag))
        report(node, `Use o componente partilhado em vez de <${tag}>.`);
      if (tag === "X")
        report(
          node,
          "Use CloseButton; evite cruzes de fecho duplicadas ou sem proteção.",
        );
      if (tag === "AppDialog") {
        stats.windows++;
        if (!attrs.has("onRequestClose"))
          report(node, "A janela deve ter uma ação de fecho explícita.");
      }
      if (tag === "ManagedForm" || tag === "Form") stats.forms++;
      if (tag === "Notice") stats.notices++;
      if (tag === "ActionButton") {
        stats.buttons++;
        const children = node.parent.children;
        const iconOnly =
          children &&
          children.filter((c) => !ts.isJsxText(c) || c.text.trim()).length >
            0 &&
          children
            .filter((c) => !ts.isJsxText(c) || c.text.trim())
            .every((c) => ts.isJsxSelfClosingElement(c));
        if (iconOnly && !attrs.has("aria-label") && !attrs.has("title"))
          report(node, "Botão apenas com ícone sem nome acessível.");
      }
    }
    ts.forEachChild(node, visit);
  }
  visit(ast);
}
function walk(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const file = path.join(dir, entry.name);
    if (entry.isDirectory()) walk(file);
    else if (file.endsWith(".tsx") && entry.name !== "InteractionUI.tsx")
      inspect(file);
  }
}
walk(root);
if (issues.length) {
  console.error(issues.join("\n"));
  process.exitCode = 1;
} else
  console.log(
    `PASS: ${stats.windows} janelas, ${stats.buttons} botões, ${stats.forms} formulários e ${stats.notices} mensagens; componentes partilhados e nomes acessíveis.`,
  );
