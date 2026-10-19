import requests, json, io, openpyxl, sys, re, time
from io import BytesIO

BASE = 'http://localhost:8000'
results = []
session = requests.Session()
# 每轮用唯一后缀，避免测试会话跨轮堆积、污染后续运行的基准会话选取
RUN_TAG = str(int(time.time()))

def test(name, ok, detail=''):
    results.append((name, ok, detail))
    status = 'PASS' if ok else 'FAIL'
    print(f'[{status}] {name}' + (f' -- {detail}' if detail else ''))

# ============================================================
# 前置：锁定「基准会话」
# 必须在任何测试动作之前取，并在会话名/季度标记上做校验。
# 原因：生命周期测试段会创建自己的会话并激活它，若基准取晚，
# 上一轮遗留的测试会话（active）会被误选为基准，导致打分测试
# 全在 ended 会话下执行而失败，且垃圾会话逐轮累积。
# ============================================================

# 1. Login page
r = session.get(f'{BASE}/login')
test('Login page loads', r.status_code == 200 and '管理后台登录' in r.text)

# 2. Unauthenticated redirect
r = session.get(f'{BASE}/', allow_redirects=False)
test('Unauthenticated redirect', r.status_code in [303, 307], f'status={r.status_code}')

# 3. Login
r = session.post(f'{BASE}/api/login', json={'username': 'admin', 'password': 'admin123'})
test('Login API', r.status_code == 200 and r.json().get('success'))

# 3.1 选取基准会话：排除测试脚本自己创建的历史会话
#     判定依据为 quarter 前缀 TEST_QUARTER（测试段创建时统一带上）。
#     同时兼容历史上未加标记的旧测试会话（按 name 兜底排除），
#     避免它们被误选为基准 —— 那是 70/77 失败的根因。
TEST_QUARTER_PREFIX = 'TESTQ'
_LEGACY_TEST_NAMES = ('生命周期测试考核', '保留数据测试')


def _is_test_session(x):
    if str(x.get('quarter') or '').startswith(TEST_QUARTER_PREFIX):
        return True
    return x.get('name') in _LEGACY_TEST_NAMES


r = session.get(f'{BASE}/api/sessions')
_all_sessions = r.json()['sessions']
_business = [x for x in _all_sessions if not _is_test_session(x)]
_orig_sid = (_business[0]['id'] if _business
             else _all_sessions[0]['id'])
print(f'>> 基准会话 id={_orig_sid} name={_business[0]["name"] if _business else "N/A"}')

# 3.2 切到基准会话并确保其为 running，否则打分测试无法进行
session.post(f'{BASE}/api/sessions/{_orig_sid}/activate')
_orig_lc = next(x.get('lifecycle') for x in
                session.get(f'{BASE}/api/sessions').json()['sessions']
                if x['id'] == _orig_sid)
# ended 是不可逆终态，无法 resume；此时换用 id 最小且非 ended 的业务会话
if _orig_lc == 'ended':
    _alt = [x for x in session.get(f'{BASE}/api/sessions').json()['sessions']
            if x.get('lifecycle') != 'ended' and not _is_test_session(x)]
    if _alt:
        _orig_sid = _alt[0]['id']
        session.post(f'{BASE}/api/sessions/{_orig_sid}/activate')
        print(f'>> 基准会话已 ended，改用 id={_orig_sid}')
    else:
        print('>> ⚠️ 所有业务会话均已 ended，生命周期测试将污染基准，请清理数据库后重跑')
# draft/paused 需推进到 running 才能打分
_cur_lc = next(x.get('lifecycle') for x in
               session.get(f'{BASE}/api/sessions').json()['sessions']
               if x['id'] == _orig_sid)
if _cur_lc in ('draft', 'paused'):
    session.post(f'{BASE}/api/sessions/{_orig_sid}/lifecycle',
                 json={'action': 'start_test' if _cur_lc == 'draft' else 'resume'})
    session.post(f'{BASE}/api/sessions/{_orig_sid}/lifecycle', json={'action': 'start'})
    print(f'>> 基准会话 {_cur_lc} -> running')

# 3.5 重置上一轮运行遗留的「已完成」状态，保证测试幂等
session.post(f'{BASE}/api/scores/1/reset')

# 4. Home page after login
r = session.get(f'{BASE}/')
test('Home page after login', r.status_code == 200 and '中层绩效考核管理系统' in r.text)

# 5. No role field
test('No role field', '角色' not in r.text and '普通考核人' not in r.text)

# 6. No category field in indicators
test('No indicator category', '类别' not in r.text)

# 7. department_ref_score as text input
test('Ref score text input', 'type="text"' in r.text and 'department_ref_score' in r.text)

# 8. Assessor template - no role column
r = session.get(f'{BASE}/api/templates/assessors')
wb = openpyxl.load_workbook(BytesIO(r.content))
ws = wb.active
headers = [c.value for c in ws[1]]
test('Assessor template no role', len(headers) == 2 and headers == ['工号', '姓名'], f'headers={headers}')

# 9. Evaluatee template
r = session.get(f'{BASE}/api/templates/evaluatees')
wb = openpyxl.load_workbook(BytesIO(r.content))
ws = wb.active
headers = [c.value for c in ws[1]]
test('Evaluatee template correct', headers == ['工号', '姓名', '部门', '职务', '组别', '部门考核得分(参考值)'], f'headers={headers}')

# 10. Import assessors (no role)
wb = openpyxl.Workbook(); ws = wb.active
ws.append(['工号', '姓名'])
ws.append(['1001', '张三'])
ws.append(['1002', '李四'])
buf = io.BytesIO(); wb.save(buf); buf.seek(0)
r = session.post(f'{BASE}/api/import/assessors', files={'file': ('t.xlsx', buf)})
d = r.json()
test('Import assessors (no role)', d.get('success') and (d['result']['created'] + d['result']['updated']) > 0, f"created={d['result']['created']}, updated={d['result']['updated']}")

# 11. Import evaluatees (text ref score)
wb = openpyxl.Workbook(); ws = wb.active
ws.append(['工号', '姓名', '部门', '职务', '组别', '部门考核得分(参考值)'])
ws.append(['2001', '王五', '财务部', '部长', '业务前端', '88.5分'])
ws.append(['2002', '赵六', '技术部', '总监', '技术中台', '优秀(90)'])
buf = io.BytesIO(); wb.save(buf); buf.seek(0)
r = session.post(f'{BASE}/api/import/evaluatees', files={'file': ('t.xlsx', buf)})
d = r.json()
test('Import evaluatees (text ref)', d.get('success') and (d['result']['created'] + d['result']['updated']) > 0, f"created={d['result']['created']}, updated={d['result']['updated']}")

# 12. Import relations
wb = openpyxl.Workbook(); ws = wb.active
ws.append(['考核人工号', '被考核人工号', '权重'])
ws.append(['1001', '2001', 0.5])
ws.append(['1002', '2001', 0.5])
ws.append(['1001', '2002', 0.6])
ws.append(['1002', '2002', 0.4])
buf = io.BytesIO(); wb.save(buf); buf.seek(0)
r = session.post(f'{BASE}/api/import/relations', files={'file': ('t.xlsx', buf)})
d = r.json()
test('Import relations', d.get('success') and (d['result']['created'] + len(d['result'].get('errors', [])) >= 0), f"created={d['result']['created']}")

# 13. Add indicator (no category)
r = session.post(f'{BASE}/api/indicators', json={'name': '领导力', 'weight': 0.5, 'max_score': 100})
test('Add indicator (no category)', r.status_code == 200)

# 14. Assess page
r = session.get(f'{BASE}/assess/1')
test('Assess page loads', r.status_code == 200)
test('Assess no category', '类别' not in r.text and '未分类' not in r.text)
# 若会话处于 ended/paused，打分页会返回锁定页，断言会全部失败并
# 附带锁定原因，便于一眼定位是「会话状态问题」而非功能回归。
_locked = '考核已结束' in r.text or '考核已暂停' in r.text or '考核尚未启动' in r.text
_hint = '会话处于锁定态(ended/paused/draft)' if _locked else ''
test('Assess has next button', '下一个' in r.text, _hint)
test('Assess has ref score', '部门考核得分(参考值)' in r.text, _hint)

# 15. Save instruction (HTML)
r = session.post(f'{BASE}/api/settings', json={'instruction': '<p style="font-size:16px;color:red;">测试说明文字</p>'})
test('Save HTML instruction', r.status_code == 200)

# 16. Assess page shows instruction
r = session.get(f'{BASE}/assess/1')
test('Assess shows instruction', '测试说明文字' in r.text, _hint)

# 17. Instruction only in list page
test('Instruction in list card', 'instruction-card' in r.text and 'evaluatee-list-card' in r.text, _hint)

# 18. Save scores (use actual indicator IDs)
r = session.get(f'{BASE}/assess/1')
import re
ind_ids = [int(m) for m in re.findall(r'data-indicator-id="(\d+)"', r.text)]
first_ind_ids = ind_ids[:2] if len(ind_ids) >= 2 else ind_ids
r = session.post(f'{BASE}/api/scores/1/batch', json={
    'evaluatee_id': 1,
    'scores': [{'indicator_id': iid, 'score': 85} for iid in first_ind_ids]
})
test('Save scores', r.status_code == 200 and r.json().get('success'),
     f"status={r.status_code}, resp={r.text[:200]}")

# 19. Progress page (no role)
r = session.get(f'{BASE}/progress')
test('Progress page loads', r.status_code == 200)
test('Progress no role column', '<th>角色</th>' not in r.text)

# 20. Results page (has employee_id, no ref score)
r = session.get(f'{BASE}/results')
test('Results page loads', r.status_code == 200)
test('Results has emp_id column', '<th>工号</th>' in r.text)
test('Results no ref score', '部门参考分' not in r.text)

# 21. Excel export (has emp_id, no ref score)
r = session.get(f'{BASE}/api/export/excel')
wb = openpyxl.load_workbook(BytesIO(r.content))
ws1 = wb['考核结果汇总']
headers1 = [c.value for c in ws1[1]]
test('Export has emp_id', '工号' in headers1)
test('Export no ref score', '部门参考分' not in headers1, f'headers={headers1}')

# 22. QR code export (no role)
r = session.get(f'{BASE}/api/export/qrcodes')
wb = openpyxl.load_workbook(BytesIO(r.content))
ws = wb.active
headers = [c.value for c in ws[1]]
test('QR export no role', '角色' not in headers, f'headers={headers}')

# 23. Score all evaluatees (use actual indicator IDs from DB)
r = session.get(f'{BASE}/api/results')
all_indicators = r.json().get('evaluatees', [])  # not ideal, use settings API instead
# Get indicators from assess page
r = session.get(f'{BASE}/assess/1')
import re
ind_ids = [int(m) for m in re.findall(r'data-indicator-id="(\d+)"', r.text)]
for eid in [1, 2]:
    session.post(f'{BASE}/api/scores/1/batch', json={
        'evaluatee_id': eid,
        'scores': [{'indicator_id': iid, 'score': 85} for iid in ind_ids]
    })

# 24. Submit
r = session.post(f'{BASE}/api/scores/1/submit')
resp = r.json() if r.headers.get('content-type','').startswith('application/json') else {}
test('Submit assessment', r.status_code == 200 and resp.get('success'), f"status={r.status_code}, resp={resp}")

# 25. Assess page shows completed
r = session.get(f'{BASE}/assess/1')
test('Assess shows completed', '考核已完成' in r.text and '感谢' in r.text)

# 26. Logout
r = session.get(f'{BASE}/logout', allow_redirects=False)
test('Logout redirect', r.status_code in [303, 307])

# 27. After logout redirect
r = session.get(f'{BASE}/', allow_redirects=False)
test('After logout needs login', r.status_code in [303, 307])

# 28. Change password
session2 = requests.Session()
session2.post(f'{BASE}/api/login', json={'username': 'admin', 'password': 'admin123'})
r = session2.post(f'{BASE}/api/change-password', json={'old_password': 'admin123', 'new_password': 'newpass456'})
test('Change password', r.status_code == 200 and r.json().get('success'))

# 29. Login with new password
r = session2.post(f'{BASE}/api/login', json={'username': 'admin', 'password': 'newpass456'})
test('New password login', r.status_code == 200 and r.json().get('success'))

# 30. Revert password
session2.post(f'{BASE}/api/change-password', json={'old_password': 'newpass456', 'new_password': 'admin123'})

# 主会话重新登录（前面测试已登出）
session.post(f'{BASE}/api/login', json={'username': 'admin', 'password': 'admin123'})

# ========== 31+. 生命周期与重置测试 ==========

# 基准会话已在脚本最开始锁定（排除测试会话名 + 已切到 running），
# 此处直接复用，避免取到本段自己创建的历史测试会话。
orig_sid = _orig_sid

# L1. 新建考核（自动成为当前会话，lifecycle=draft）
r = session.post(f'{BASE}/api/sessions', json={'name': '生命周期测试考核', 'quarter': f'{TEST_QUARTER_PREFIX}-LT-{RUN_TAG}'})
test('Create lifecycle session', r.status_code == 200)
r = session.get(f'{BASE}/api/sessions')
cur = next(s for s in r.json()['sessions'] if s['status'] == 'active')
lt_sid = cur['id']
test('New session lifecycle=draft', cur.get('lifecycle') == 'draft', f"lifecycle={cur.get('lifecycle')}")

# L2. draft 状态打分页锁定
r = session.get(f'{BASE}/assess/1')
test('Draft locks assess page', 'status-lock-card' in r.text and '考核尚未启动' in r.text)

# L3. draft 状态打分 API 拒绝
r = session.post(f'{BASE}/api/scores/1/batch', json={'evaluatee_id': 1, 'scores': [{'indicator_id': 1, 'score': 85}]})
test('Draft blocks score API', r.status_code == 403, f'status={r.status_code}')

# L4. 未登录不能操作生命周期
anon = requests.Session()
r = anon.post(f'{BASE}/api/sessions/{lt_sid}/lifecycle', json={'action': 'start_test'})
test('Lifecycle API needs login', r.status_code == 401)

# L5. 启动测试模式
r = session.post(f'{BASE}/api/sessions/{lt_sid}/lifecycle', json={'action': 'start_test'})
d = r.json()
test('Start test mode', r.status_code == 200 and d.get('lifecycle') == 'testing')

# L6. 测试模式横幅 + 可正常打分
r = session.get(f'{BASE}/assess/1')
test('Testing banner shown', 'test-mode-banner' in r.text and '测试模式' in r.text)

# 导入考核关系到新会话
wb = openpyxl.Workbook(); ws = wb.active
ws.append(['考核人工号', '被考核人工号', '权重'])
ws.append(['1001', '2001', 0.5])
ws.append(['1002', '2001', 0.5])
ws.append(['1001', '2002', 0.6])
ws.append(['1002', '2002', 0.4])
buf = io.BytesIO(); wb.save(buf); buf.seek(0)
r = session.post(f'{BASE}/api/import/relations', files={'file': ('t.xlsx', buf)})
test('Import relations to new session', r.json().get('success'))

# 测试模式下打分并提交
r = session.get(f'{BASE}/assess/1')
ind_ids = [int(m) for m in re.findall(r'data-indicator-id="(\d+)"', r.text)]
for eid in [1, 2]:
    session.post(f'{BASE}/api/scores/1/batch', json={
        'evaluatee_id': eid, 'scores': [{'indicator_id': iid, 'score': 80} for iid in ind_ids]
    })
r = session.post(f'{BASE}/api/scores/1/submit')
test('Submit in testing mode', r.status_code == 200 and r.json().get('success'))

# L7. 测试转正式：清空测试数据
r = session.post(f'{BASE}/api/sessions/{lt_sid}/lifecycle', json={'action': 'start', 'clear_scores': True})
d = r.json()
test('Start formal with clear', r.status_code == 200 and d.get('lifecycle') == 'running' and d.get('cleared_scores', 0) > 0,
     f"cleared={d.get('cleared_scores')}")
r = session.get(f'{BASE}/assess/1')
test('Scores cleared after formal start', '已评分' not in r.text and '待评分' in r.text)
r = session.get(f'{BASE}/progress')
test('Progress reset to not started', '未开始' in r.text)

# L8. 暂停
r = session.post(f'{BASE}/api/sessions/{lt_sid}/lifecycle', json={'action': 'pause'})
test('Pause session', r.status_code == 200 and r.json().get('lifecycle') == 'paused')
r = session.get(f'{BASE}/assess/1')
test('Paused locks assess page', '考核已暂停' in r.text and '待考核人员' not in r.text)
r = session.post(f'{BASE}/api/scores/1/batch', json={'evaluatee_id': 1, 'scores': [{'indicator_id': ind_ids[0], 'score': 85}]})
test('Paused blocks score API', r.status_code == 403)

# L9. 恢复（回到 running）
r = session.post(f'{BASE}/api/sessions/{lt_sid}/lifecycle', json={'action': 'resume'})
test('Resume session', r.status_code == 200 and r.json().get('lifecycle') == 'running')

# L10. 重新打分并提交
for eid in [1, 2]:
    session.post(f'{BASE}/api/scores/1/batch', json={
        'evaluatee_id': eid, 'scores': [{'indicator_id': iid, 'score': 85} for iid in ind_ids]
    })
r = session.post(f'{BASE}/api/scores/1/submit')
test('Re-submit after resume', r.status_code == 200 and r.json().get('success'))
r = session.get(f'{BASE}/progress')
test('Progress shows completed', '已完成' in r.text and '重置打分' in r.text)

# L11. 重置考核人（归档原打分，解锁重新打分）
r = session.post(f'{BASE}/api/scores/1/reset')
d = r.json()
test('Reset assessor', r.status_code == 200 and d.get('success') and d.get('archived', 0) > 0,
     f"archived={d.get('archived')}")
r = session.get(f'{BASE}/assess/1')
test('Assessor unlocked after reset', '考核进行中' in r.text)
test('Old scores prefilled after reset', '已评分' in r.text)
r = session.get(f'{BASE}/progress')
# 跨轮运行时重置次数会累积，故用正则匹配「已重置N次」而非固定文案
test('Reset count shown', re.search(r'已重置\d+次', r.text) is not None)

# L12. 重置后重新打分（改分）并再次提交
for eid in [1, 2]:
    session.post(f'{BASE}/api/scores/1/batch', json={
        'evaluatee_id': eid, 'scores': [{'indicator_id': iid, 'score': 92} for iid in ind_ids]
    })
r = session.post(f'{BASE}/api/scores/1/submit')
test('Re-submit after reset', r.status_code == 200 and r.json().get('success'))
r = session.get(f'{BASE}/assess/1')
test('Completed again after reset', '考核已完成' in r.text)

# L12b. 归档记录查看（历史 API）
r = session.get(f'{BASE}/api/scores/1/history')
d = r.json()
# 注意：不要断言批次总数 == 1。考核人 1 在多次运行中可能被重置过，
# 历史表会累积多条批次；只断言「至少有本轮批次」且结构正确。
_batches = d.get('batches', [])
test('History API returns batches', r.status_code == 200 and d.get('success')
     and len(_batches) >= 1,
     f"batches={len(_batches)}")
batch = _batches[0]
test('History batch has evaluatees', len(batch.get('evaluatees', [])) == 2)
ev0 = batch['evaluatees'][0]
test('History evaluatee has indicators', len(ev0.get('indicators', [])) == len(ind_ids))
expected_total = round(sum(i['indicator_weight'] * 85 for i in ev0.get('indicators', [])), 2)
test('History weighted total correct', ev0.get('weighted_total') == expected_total,
     f"total={ev0.get('weighted_total')}, expected={expected_total}")
test('History reset_count positive', (d.get('reset_count') or 0) >= 1,
     f"reset_count={d.get('reset_count')}")
# 未登录访问应 401
r2 = requests.get(f'{BASE}/api/scores/1/history')
test('History API requires login', r2.status_code == 401)
# 进度页有查看记录按钮
r = session.get(f'{BASE}/progress')
test('Progress shows history button', '查看记录' in r.text)
# 不存在的考核人 404
r = session.get(f'{BASE}/api/scores/99999/history')
test('History 404 for unknown assessor', r.status_code == 404)
# 无归档记录的考核人返回空批次
r = session.get(f'{BASE}/api/scores/2/history')
d2 = r.json()
test('History empty for no-reset assessor', r.status_code == 200 and d2.get('success')
     and len(d2.get('batches', [])) == 0)

# ========== 访问令牌安全测试（防 IDOR）============
# 原实现用自增 id 作为打分链接，改 URL 数字即可进他人页面并覆盖分数。
# 现改为不可猜测随机令牌，此处验证越权确实被拦得住。
_anon2 = requests.Session()  # 未登录的全新会话

# 取考核人1 的令牌：借助已登录的管理员会话读二维码导出表中的「扫码链接」列
r = session.get(f'{BASE}/api/export/qrcodes')
_wb = openpyxl.load_workbook(BytesIO(r.content))
_ws = _wb.active
_tok1 = None
_row2 = 2
while _row2 <= _ws.max_row:
    _nm = _ws.cell(row=_row2, column=1).value
    _url = _ws.cell(row=_row2, column=4).value or ''
    if _nm == '张三' and '/s/' in str(_url):
        _tok1 = str(_url).split('/s/')[-1].strip()
        break
    _row2 += 1
test('Token exported in QR sheet', bool(_tok1) and len(_tok1) >= 32,
     f"token_len={len(_tok1) if _tok1 else 0}")

# 1) 令牌版链接可正常访问
r = _anon2.get(f'{BASE}/s/{_tok1}')
test('Token URL opens assess page', r.status_code == 200 and '考核' in r.text)

# 2) 伪造令牌应被拒绝（返回错误页而非打分表单）
r = _anon2.get(f'{BASE}/s/deadbeefdeadbeefdeadbeefdeadbeef')
test('Fake token rejected', '失效' in r.text and 'data-indicator-id' not in r.text,
     f'status={r.status_code}')

# 3) 旧的 id 直链（无令牌、未登录）应被拒绝 —— 这就是原漏洞
r = _anon2.get(f'{BASE}/assess/1')
test('ID-only URL blocked (IDOR fixed)',
     '失效' in r.text and 'data-indicator-id' not in r.text, f'status={r.status_code}')

# 4) 令牌与 id 不匹配（拿1号的令牌访问2号）应被拒绝
r = _anon2.get(f'{BASE}/assess/2?t={_tok1}')
test('Token mismatch blocked', '失效' in r.text and 'data-indicator-id' not in r.text,
     f'status={r.status_code}')

# 5) 未登录直接调打分接口应 403
r = _anon2.post(f'{BASE}/api/scores/1/batch', json={
    'evaluatee_id': 1, 'scores': [{'indicator_id': 1, 'score': 99}]
})
test('Unauthenticated score API blocked', r.status_code == 403, f'status={r.status_code}')

# 6) 未登录直接调提交接口应 403
r = _anon2.post(f'{BASE}/api/scores/1/submit')
test('Unauthenticated submit blocked', r.status_code == 403, f'status={r.status_code}')

# 7) 非登录用户带正确令牌可打分（正常扫码路径）
#    注意：需先重置该考核人，否则若其已提交则会被「考核已完成」拦下（400）
session.post(f'{BASE}/api/scores/1/reset')
r = _anon2.post(f'{BASE}/api/scores/1/batch',
                headers={'X-Access-Token': _tok1},
                json={'evaluatee_id': 1,
                      'scores': [{'indicator_id': int(i), 'score': 86} for i in ind_ids[:1]]})
test('Token allows scoring', r.status_code == 200, f'status={r.status_code}, resp={r.text[:150]}')

# 8) 重置令牌后旧令牌立即失效
_r = session.post(f'{BASE}/api/qrcode/1/reset')
test('Reset token API works', _r.status_code == 200 and _r.json().get('success'),
     f'status={_r.status_code}')
_new_tok = _r.json().get('url', '').split('/s/')[-1]
test('Reset returns new token', bool(_new_tok) and _new_tok != _tok1)
r = _anon2.get(f'{BASE}/s/{_tok1}')
test('Old token invalid after reset', '失效' in r.text, f'status={r.status_code}')
r = _anon2.get(f'{BASE}/s/{_new_tok}')
test('New token works after reset', r.status_code == 200 and '考核' in r.text)

# L13. 结束考核（不可逆）
r = session.post(f'{BASE}/api/sessions/{lt_sid}/lifecycle', json={'action': 'end'})
test('End session', r.status_code == 200 and r.json().get('lifecycle') == 'ended')
r = session.get(f'{BASE}/assess/1')
test('Ended locks assess page', '考核已结束' in r.text)
r = session.post(f'{BASE}/api/scores/1/batch', json={'evaluatee_id': 1, 'scores': [{'indicator_id': ind_ids[0], 'score': 85}]})
test('Ended blocks score API', r.status_code == 403)
r = session.post(f'{BASE}/api/sessions/{lt_sid}/lifecycle', json={'action': 'resume'})
test('Ended cannot resume', r.status_code == 400)
r = session.post(f'{BASE}/api/sessions/{lt_sid}/lifecycle', json={'action': 'end'})
test('Ended cannot end again', r.status_code == 400)

# L14. 测试转正式：保留数据分支
r = session.post(f'{BASE}/api/sessions', json={'name': '保留数据测试', 'quarter': f'{TEST_QUARTER_PREFIX}-KP-{RUN_TAG}'})
kp_sid = None
r = session.get(f'{BASE}/api/sessions')
kp_sid = next(s['id'] for s in r.json()['sessions'] if s['status'] == 'active')
session.post(f'{BASE}/api/sessions/{kp_sid}/lifecycle', json={'action': 'start_test'})
buf2 = io.BytesIO()
wb2 = openpyxl.Workbook(); ws2 = wb2.active
ws2.append(['考核人工号', '被考核人工号', '权重'])
ws2.append(['1001', '2001', 1.0])
wb2.save(buf2); buf2.seek(0)
r = session.post(f'{BASE}/api/import/relations', files={'file': ('t.xlsx', buf2)})
test('Import relations to KP session', r.json().get('success'))
session.post(f'{BASE}/api/scores/1/batch', json={
    'evaluatee_id': 1, 'scores': [{'indicator_id': iid, 'score': 77} for iid in ind_ids]
})
r = session.post(f'{BASE}/api/sessions/{kp_sid}/lifecycle', json={'action': 'start', 'clear_scores': False})
d = r.json()
test('Start formal keeping data', r.status_code == 200 and d.get('cleared_scores', 0) == 0)
r = session.get(f'{BASE}/assess/1')
test('Scores retained', '已评分' in r.text)
session.post(f'{BASE}/api/sessions/{kp_sid}/lifecycle', json={'action': 'end'})

# ============================================================
# 跨季度数据隔离测试：同一被考核人在不同季度的部门参考分相互独立
# ============================================================

def _create_isolation_session(name_suffix, quarter):
    r = session.post(f'{BASE}/api/sessions', json={
        'name': f'隔离测试-{name_suffix}-{RUN_TAG}',
        'quarter': quarter
    })
    r = session.get(f'{BASE}/api/sessions')
    return next(s['id'] for s in r.json()['sessions'] if s['status'] == 'active')


def _import_evaluatee_with_ref(sid, emp_id, name, ref_score):
    # 先激活目标会话
    session.post(f'{BASE}/api/sessions/{sid}/activate')
    buf = io.BytesIO()
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(['工号', '姓名', '部门', '职务', '组别', '部门考核得分(参考值)'])
    ws.append([emp_id, name, '测试部', '经理', 'A组', str(ref_score)])
    wb.save(buf); buf.seek(0)
    r = session.post(f'{BASE}/api/import/evaluatees', files={'file': ('t.xlsx', buf)})
    return r.json()


iso_emp_id = f'ISO{RUN_TAG[-6:]}'
iso_name = f'隔离员工{RUN_TAG[-6:]}'

# 创建 Q1 并写入 ref=80
sid_q1 = _create_isolation_session('Q1', f'{TEST_QUARTER_PREFIX}-ISO-Q1-{RUN_TAG}')
_import_evaluatee_with_ref(sid_q1, iso_emp_id, iso_name, 80)
# 结果页应展示 Q1 ref score
r = session.get(f'{BASE}/api/results')
iso_eval_q1 = next((e for e in r.json()['evaluatees'] if e.get('employee_id') == iso_emp_id), None)
test('Session isolation: Q1 ref score captured',
     iso_eval_q1 is not None and iso_eval_q1.get('department_ref_score') == '80',
     f"found={iso_eval_q1 is not None}, score={iso_eval_q1.get('department_ref_score') if iso_eval_q1 else None}")

# 创建 Q2 并写入 ref=90
sid_q2 = _create_isolation_session('Q2', f'{TEST_QUARTER_PREFIX}-ISO-Q2-{RUN_TAG}')
_import_evaluatee_with_ref(sid_q2, iso_emp_id, iso_name, 90)
# 在 Q2 验证 ref=90
r = session.get(f'{BASE}/api/results')
iso_eval_q2 = next((e for e in r.json()['evaluatees'] if e.get('employee_id') == iso_emp_id), None)
test('Session isolation: Q2 ref score captured',
     iso_eval_q2 is not None and iso_eval_q2.get('department_ref_score') == '90',
     f"score={iso_eval_q2.get('department_ref_score') if iso_eval_q2 else None}")

# 切换回 Q1，验证仍为 80
session.post(f'{BASE}/api/sessions/{sid_q1}/activate')
r = session.get(f'{BASE}/api/results')
iso_eval_q1_again = next((e for e in r.json()['evaluatees'] if e.get('employee_id') == iso_emp_id), None)
test('Session isolation: Q1 unchanged after Q2 update',
     iso_eval_q1_again is not None and iso_eval_q1_again.get('department_ref_score') == '80',
     f"score={iso_eval_q1_again.get('department_ref_score') if iso_eval_q1_again else None}")

# 结束隔离测试会话
session.post(f'{BASE}/api/sessions/{sid_q1}/lifecycle', json={'action': 'end'})
session.post(f'{BASE}/api/sessions/{sid_q2}/lifecycle', json={'action': 'end'})

# 收尾：切回基准会话，并保证其处于可打分状态（running），
# 否则下次运行第 1 段打分测试会因会话锁定而全部失败。
session.post(f'{BASE}/api/sessions/{orig_sid}/activate')
r = session.get(f'{BASE}/api/sessions')
_final = r.json()['sessions']
cur_final = next((s for s in _final if s['status'] == 'active'), None)
test('Restored original session', cur_final is not None and cur_final['id'] == orig_sid)

_lc_final = cur_final.get('lifecycle') if cur_final else None
if _lc_final == 'ended':
    print(f'>> ⚠️ 基准会话 id={orig_sid} 仍为 ended，下次运行前请清理测试数据')
elif _lc_final in ('draft', 'paused'):
    session.post(f'{BASE}/api/sessions/{orig_sid}/lifecycle',
                 json={'action': 'start_test' if _lc_final == 'draft' else 'resume'})
    session.post(f'{BASE}/api/sessions/{orig_sid}/lifecycle', json={'action': 'start'})
    print(f'>> 基准会话已恢复为 running')
test('Baseline session is scorable', _lc_final == 'running',
     f'lifecycle={_lc_final}')

print()
passed = sum(1 for _,ok,_ in results if ok)
print(f'=== {passed}/{len(results)} passed ===')
for name, ok, detail in results:
    if not ok:
        print(f'  FAILED: {name} -- {detail}')
