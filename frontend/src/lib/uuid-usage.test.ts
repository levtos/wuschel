import { readFileSync, readdirSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';
import ts from 'typescript';
import { expect, it } from 'vitest';

it('keeps every production randomUUID reference inside the HTTP-safe helper', () => {
  const root = resolve('src');
  const found: string[] = [];
  function visitDir(dir: string) {
    for (const item of readdirSync(dir, { withFileTypes: true })) {
      const path = join(dir, item.name);
      if (item.isDirectory()) { visitDir(path); continue; }
      if (!/\.tsx?$/.test(path) || /\.test\./.test(path) || relative(root, path).replaceAll('\\', '/') === 'lib/uuid.ts') continue;
      const source = ts.createSourceFile(path, readFileSync(path, 'utf8'), ts.ScriptTarget.Latest, true);
      function visit(node: ts.Node) {
        if ((ts.isPropertyAccessExpression(node) && node.name.text === 'randomUUID') || (ts.isElementAccessExpression(node) && ts.isStringLiteral(node.argumentExpression) && node.argumentExpression.text === 'randomUUID')) found.push(relative(root, path));
        ts.forEachChild(node, visit);
      }
      visit(source);
    }
  }
  visitDir(root);
  expect(found).toEqual([]);
});
