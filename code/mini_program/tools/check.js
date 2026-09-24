/**
 * 小程序静态自检（离线，不依赖微信开发者工具）
 *
 * 做三件事：
 *   1. 检查 WXML 里用到的所有自定义组件是否已在 app.json / 页面 json 里注册；
 *   2. 检查 WXML 里绑定的事件处理函数是否在对应 page/component 的 JS 里存在；
 *   3. 检查 app.json 的 pages 列表与实际文件是否一一对应。
 *
 * 用法： node tools/check.js
 * 退出码非 0 表示发现问题（可用于 CI）。
 */

const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const problems = [];

function read(p) {
  return fs.readFileSync(p, 'utf8');
}

function exists(p) {
  try {
    fs.accessSync(p);
    return true;
  } catch (e) {
    return false;
  }
}

function walk(dir, out) {
  out = out || [];
  fs.readdirSync(dir).forEach((name) => {
    const full = path.join(dir, name);
    const st = fs.statSync(full);
    if (st.isDirectory()) {
      if (name === 'node_modules' || name === 'ui' || name === 'tools') return;
      walk(full, out);
    } else {
      out.push(full);
    }
  });
  return out;
}

const allFiles = walk(ROOT);

/* ---------------------------------------------------------- app.json */

const appJson = JSON.parse(read(path.join(ROOT, 'app.json')));
const globalComponents = appJson.usingComponents || {};

(appJson.pages || []).forEach((p) => {
  ['.js', '.wxml', '.json'].forEach((ext) => {
    const f = path.join(ROOT, p + ext);
    if (!exists(f)) problems.push(`app.json 声明了页面 ${p}，但缺少 ${p + ext}`);
  });
});

/* ---------------------------------------------------------- 逐文件检查 */

const wxmlFiles = allFiles.filter((f) => f.endsWith('.wxml'));

wxmlFiles.forEach((wxmlPath) => {
  const dir = path.dirname(wxmlPath);
  const base = wxmlPath.slice(0, -5);
  const jsonPath = base + '.json';
  const jsPath = base + '.js';
  const src = read(wxmlPath);

  // --- 1. 自定义组件注册 ---
  let localComponents = {};
  if (exists(jsonPath)) {
    try {
      const j = JSON.parse(read(jsonPath));
      localComponents = j.usingComponents || {};
    } catch (e) {
      problems.push(`JSON 解析失败: ${path.relative(ROOT, jsonPath)} — ${e.message}`);
    }
  } else {
    problems.push(`缺少 json: ${path.relative(ROOT, jsonPath)}`);
  }

  const tags = new Set();
  const tagRe = /<(q-[a-z0-9-]+)/g;
  let m;
  while ((m = tagRe.exec(src))) tags.add(m[1]);

  tags.forEach((tag) => {
    if (!globalComponents[tag] && !localComponents[tag]) {
      problems.push(`${path.relative(ROOT, wxmlPath)} 使用了未注册组件 <${tag}>`);
    }
  });

  // --- 2. 事件处理函数 ---
  if (!exists(jsPath)) {
    problems.push(`缺少 js: ${path.relative(ROOT, jsPath)}`);
    return;
  }
  const js = read(jsPath);
  const handlers = new Set();
  const evRe = /\b(?:bind|catch|capture-bind|capture-catch)[:]?([a-zA-Z]+)\s*=\s*"([^"{}]+)"/g;
  while ((m = evRe.exec(src))) handlers.add(m[2].trim());

  handlers.forEach((h) => {
    // 允许 onXxx / xxx 形式的顶层方法定义
    const defined =
      new RegExp(`(^|[\\s,{])${h}\\s*\\(`, 'm').test(js) ||
      new RegExp(`${h}\\s*:\\s*function`, 'm').test(js) ||
      new RegExp(`${h}\\s*:\\s*\\(`, 'm').test(js);
    if (!defined) {
      problems.push(`${path.relative(ROOT, wxmlPath)} 绑定了 ${h}，但 ${path.basename(jsPath)} 里没有该方法`);
    }
  });
});

/* ---------------------------------------------------------- JS require 路径 */

allFiles
  .filter((f) => f.endsWith('.js'))
  .forEach((f) => {
    const src = read(f);
    const re = /require\(\s*['"](\.[^'"]+)['"]\s*\)/g;
    let m;
    while ((m = re.exec(src))) {
      const target = path.resolve(path.dirname(f), m[1]);
      if (!exists(target) && !exists(target + '.js') && !exists(path.join(target, 'index.js'))) {
        problems.push(`${path.relative(ROOT, f)} require 的 ${m[1]} 不存在`);
      }
    }
  });

/* ---------------------------------------------------------- 输出 */

const report = problems.length
  ? `发现 ${problems.length} 个问题：\n\n` + problems.map((p) => ' - ' + p).join('\n') + '\n'
  : '自检通过：组件注册、事件绑定、require 路径、页面清单均一致。\n';

// 若传入第二个参数则把报告写进文件（便于在 CI / 无终端环境下取结果）
if (process.argv[2]) {
  fs.writeFileSync(process.argv[2], report, 'utf8');
}

console.log(report);
process.exit(problems.length ? 1 : 0);
