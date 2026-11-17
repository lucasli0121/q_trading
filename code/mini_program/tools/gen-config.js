/**
 * 构建时配置注入：由 config/index.template.js 生成 config/index.js
 *
 * 取值优先级：环境变量 > config/.env.local > 脚本缺省值。
 * 生成产物 config/index.js 已被 .gitignore 排除，不入库。
 *
 * 用法：
 *   node tools/gen-config.js                     # 用 .env.local 或环境变量生成
 *   MP_BASE_URL=https://api.example.com node tools/gen-config.js
 *   MP_ENV=production node tools/gen-config.js   # 强制校验 https/wss
 *   node tools/gen-config.js --out /tmp/index.js # 只输出到指定文件（自检用）
 *
 * 退出码非 0 表示配置不合法（可用于 CI / 发布前置检查）。
 */

const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const TEMPLATE = path.join(ROOT, 'config', 'index.template.js');
const DEFAULT_OUT = path.join(ROOT, 'config', 'index.js');
const ENV_FILE = path.join(ROOT, 'config', '.env.local');
const DEFAULT_BASE_URL = 'http://127.0.0.1:8000';
const WS_PATH = '/ws/trade_signal';

function readEnvFile(file) {
  const out = {};
  if (!fs.existsSync(file)) return out;
  for (const raw of fs.readFileSync(file, 'utf8').split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith('#')) continue;
    const eq = line.indexOf('=');
    if (eq < 0) continue;
    const key = line.slice(0, eq).trim();
    let value = line.slice(eq + 1).trim();
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }
    out[key] = value;
  }
  return out;
}

/**
 * 解析单个配置项：环境变量优先，其次 .env.local。
 * 返回 undefined 表示两处都没出现该键（调用方据此取缺省值）。
 */
function pick(key, fileEnv) {
  if (Object.prototype.hasOwnProperty.call(process.env, key)) {
    return process.env[key];
  }
  if (Object.prototype.hasOwnProperty.call(fileEnv, key)) {
    return fileEnv[key];
  }
  return undefined;
}

function deriveWsUrl(baseUrl) {
  const m = /^(https?):\/\/([^/]+)/.exec(baseUrl);
  if (!m) return '';
  const scheme = m[1] === 'https' ? 'wss' : 'ws';
  return `${scheme}://${m[2]}${WS_PATH}`;
}

function parseArgs(argv) {
  const args = { out: DEFAULT_OUT };
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === '--out') {
      const value = argv[i + 1];
      if (!value) throw new Error('--out 需要一个文件路径');
      args.out = path.resolve(value);
      i += 1;
    } else if (argv[i] === '--help' || argv[i] === '-h') {
      args.help = true;
    } else {
      throw new Error(`未知参数: ${argv[i]}`);
    }
  }
  return args;
}

function fail(message) {
  process.stderr.write(`[gen-config] ${message}\n`);
  process.exit(1);
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    process.stdout.write('用法: node tools/gen-config.js [--out <文件>]\n');
    return;
  }

  if (!fs.existsSync(TEMPLATE)) {
    fail(`模板不存在: ${TEMPLATE}`);
  }

  const fileEnv = readEnvFile(ENV_FILE);

  const baseUrl = (pick('MP_BASE_URL', fileEnv) || DEFAULT_BASE_URL).replace(/\/+$/, '');
  const wsOverride = pick('MP_WS_URL', fileEnv);
  // 显式留空 = 关闭 WS；未配置 = 由 BASE_URL 推导
  const wsUrl = wsOverride === undefined ? deriveWsUrl(baseUrl) : wsOverride.trim();
  const wxAppId = (pick('MP_WX_APPID', fileEnv) || '').trim();
  const envName = (pick('MP_ENV', fileEnv) || 'development').trim();

  if (!/^https?:\/\//.test(baseUrl)) {
    fail(`MP_BASE_URL 必须以 http:// 或 https:// 开头，当前为 ${JSON.stringify(baseUrl)}`);
  }
  if (wsUrl && !/^wss?:\/\//.test(wsUrl)) {
    fail(`MP_WS_URL 必须以 ws:// 或 wss:// 开头，当前为 ${JSON.stringify(wsUrl)}`);
  }
  if (envName === 'production') {
    if (!baseUrl.startsWith('https://')) {
      fail(`production 环境要求 HTTPS，当前 MP_BASE_URL=${baseUrl}`);
    }
    if (wsUrl && !wsUrl.startsWith('wss://')) {
      fail(`production 环境要求 WSS，当前 MP_WS_URL=${wsUrl}`);
    }
  }

  const source = fs
    .readFileSync(TEMPLATE, 'utf8')
    // 去掉模板自身的说明注释块，产物只保留「自动生成」头
    .replace(/^\/\*[\s\S]*?\*\/\s*/, '')
    .replace("'__BASE_URL__'", JSON.stringify(baseUrl))
    .replace("'__WS_URL__'", JSON.stringify(wsUrl))
    .replace("'__WX_APPID__'", JSON.stringify(wxAppId));

  // 只校验赋值处的带引号占位符；文件头注释里裸写的占位符名不算
  if (
    source.includes("'__BASE_URL__'") ||
    source.includes("'__WS_URL__'") ||
    source.includes("'__WX_APPID__'")
  ) {
    fail('模板中的占位符未被全部替换，请检查 config/index.template.js');
  }

  const header = [
    '/**',
    ' * ⚠️ 本文件由 tools/gen-config.js 于构建时生成，请勿手工修改。',
    ' * 修改配置：编辑 config/index.template.js 或 config/.env.local 后重新生成。',
    ` * 环境：${envName}`,
    ' */',
    ''
  ].join('\n');

  fs.mkdirSync(path.dirname(args.out), { recursive: true });
  fs.writeFileSync(args.out, header + source, 'utf8');

  process.stdout.write(
    [
      `[gen-config] 已生成 ${path.relative(ROOT, args.out) || args.out}`,
      `  MP_ENV   = ${envName}`,
      `  BASE_URL = ${baseUrl}`,
      `  WS_URL   = ${wsUrl || '(关闭，走 HTTP 轮询)'}`,
      `  WX_APPID = ${wxAppId || '(空)'}`,
      ''
    ].join('\n')
  );
}

try {
  main();
} catch (err) {
  fail(err.message);
}
