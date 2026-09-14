// 门户密码加密的「真」oracle：直接加载门户线上两份 JS，调用页面自己的
// encryptedPassword()，与 login_bch.js 的调用方式一致。
//
//   security.js        —— ohdave RSAUtils（RSA 填充算法，本身不反转）
//   AuthInterFace.js   —— encryptedPassword()，里面 `password.split("").reverse().join("")`
//
// 用法：
//   node tools/portal_crypto_oracle.js <security.js> <AuthInterFace.js>
// 两份文件可从门户下载：
//   https://ismu.shmtu.edu.cn:8443/eportal/interface/index_files/js/security.js
//   https://ismu.shmtu.edu.cn:8443/eportal/interface/index_files/js/AuthInterFace.js
//
// ⚠️ 不要只用 security.js 当 oracle 自建一个「拼接后直接 RSA」的包装 —— 那样会漏掉
// 反转，而结果照样逐字节自洽，极易把错误结论写进单测。
const fs = require('fs');
const vm = require('vm');

const securityPath = process.argv[2] || '/tmp/live_security.js';
const authFacePath = process.argv[3] || '/tmp/live_AuthInterFace.js';

// 与门户 pageInfo 实测一致的 1024 位公钥（本校长寿命密钥）
const MODULUS =
  '94dd2a8675fb779e6b9f7103698634cd400f27a154afa67af6166a43fc26417222a79506d34cacc7641946abda1785b7' +
  'acf9910ad6a0978c91ec84d40b71d2891379af19ffb333e7517e390bd26ac312fe940c340466b4a5d4af1d65c3b5944' +
  '078f96a1a51a5a53e4bc302818b7c9f63c4a1b07bd7d874cef1c3d4b2f5eb7871';
const EXPONENT = '10001';

// AuthInterFace.js 的 encryptedPassword() 从隐藏域读公钥，这里用 pageInfo 的真实值喂它
const FIELDS = { publicKeyExponent: EXPONENT, publicKeyModulus: MODULUS };

const ctx = {
  console,
  window: {},
  document: { getElementById: (id) => ({ value: FIELDS[id] !== undefined ? FIELDS[id] : '' }) },
};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(securityPath, 'utf8'), ctx);
// security.js 是 `(function($w){...})(window)`，把 RSAUtils 挂在 window 上；
// 浏览器里 window 就是全局对象，这里手工补一下，AuthInterFace.js 才能直接引用。
ctx.RSAUtils = ctx.window.RSAUtils;
vm.runInContext(fs.readFileSync(authFacePath, 'utf8'), ctx);

if (typeof ctx.encryptedPassword !== 'function') {
  throw new Error('AuthInterFace.js 里没有定义 encryptedPassword，请检查文件是否完整');
}

// login_bch.js 的真实调用：encryptedPassword(password + ">" + macString)
function P(pwd, mac) {
  return ctx.encryptedPassword(pwd + '>' + mac);
}

const MAC = '67d1ff70d8b083fe77eff0367912afaa';
console.log('MyPass123 + MAC  :', P('MyPass123', MAC));
console.log('x + DEFAULT_MAC  :', P('x', '111111111'));
console.log('126*a + 111111111:', P('a'.repeat(126), '111111111'));
