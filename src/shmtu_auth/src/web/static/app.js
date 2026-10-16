let csrf = '';
const $ = (id) => document.getElementById(id);
function notice(message) { $('notice').textContent = message; }
async function api(path, body) {
  const response = await fetch('/api/' + path, body === undefined ? {} : {
    method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf}, body: JSON.stringify(body)
  });
  let data = {}; try { data = await response.json(); } catch (_) {}
  if (!response.ok) {
    if (response.status === 401 && path !== 'login') showLogin();
    throw new Error(data.error || (response.status === 401 ? '请重新登录' : '请求失败，请稍后重试'));
  }
  return data;
}
function showLogin() { $('login-panel').hidden = false; $('settings-panel').hidden = true; }
function addUser(user = {}) {
  const row = $('user-template').content.firstElementChild.cloneNode(true);
  row.querySelector('.user-id').value = user.id || '';
  row.querySelector('.user-password').required = !user.has_password;
  row.querySelector('.user-password').placeholder = user.has_password ? '已设置，留空保留' : '输入密码';
  row.querySelector('.user-id').addEventListener('input', () => {
    row.querySelector('.user-password').required = !user.has_password || row.querySelector('.user-id').value !== user.id;
  });
  row.querySelector('.remove').onclick = () => { row.remove(); emptyState(); };
  $('users').append(row); emptyState();
}
function emptyState() { $('empty-users').hidden = !!$('users').children.length; }
async function refreshStatus() {
  if ($('settings-panel').hidden) return;
  try {
    const state = await api('status');
    $('network-status').textContent = state.message;
    $('status-dot').classList.toggle('online', state.phase === 'online');
  } catch (error) { notice(error.message); }
}
async function showSettings() {
  const accounts = await api('accounts');
  $('users').replaceChildren(); accounts.users.forEach(addUser); emptyState();
  $('login-panel').hidden = true; $('settings-panel').hidden = false;
  await refreshStatus();
}
$('login-form').onsubmit = async (event) => {
  event.preventDefault(); const button = event.submitter; button.disabled = true;
  try { const result = await api('login', {password: $('admin-password').value}); csrf = result.csrf; $('admin-password').value = ''; notice(''); await showSettings(); }
  catch (error) { notice(error.message); } finally { button.disabled = false; }
};
$('add-user').onclick = () => addUser();
$('accounts-form').onsubmit = async (event) => {
  event.preventDefault(); $('save').disabled = true;
  const users = Array.from($('users').children, row => ({id: row.querySelector('.user-id').value.trim(), password: row.querySelector('.user-password').value}));
  try { await api('accounts', {users}); await showSettings(); notice('已保存，认证程序将自动应用。'); }
  catch (error) { notice(error.message); } finally { $('save').disabled = false; }
};
$('password-form').onsubmit = async (event) => {
  event.preventDefault(); const button = event.submitter; button.disabled = true;
  try {
    const result = await api('password', {current: $('current-password').value, password: $('new-password').value});
    csrf = result.csrf; $('password-form').reset(); notice('管理密码已更新，其他登录会话已失效。');
  } catch (error) { notice(error.message); } finally { button.disabled = false; }
};
$('logout').onclick = async () => { try { await api('logout', {}); csrf = ''; showLogin(); notice('已退出登录。'); } catch (error) { notice(error.message); } };
(async () => { try { const state = await api('session'); csrf = state.csrf; if (state.authenticated) await showSettings(); else showLogin(); } catch (error) { showLogin(); notice(error.message); } })();
setInterval(refreshStatus, 5000);
