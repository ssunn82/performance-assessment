// 中层绩效考核系统 - 管理后台 JS

// 标签页切换
document.querySelectorAll('.tab').forEach(tab => {
    if (tab.dataset.tab) {
        tab.addEventListener('click', () => {
            document.querySelectorAll('.tab').forEach(t => {
                if (t.dataset.tab) t.classList.remove('active');
            });
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            tab.classList.add('active');
            const target = document.getElementById(tab.dataset.tab + '-tab');
            if (target) target.classList.add('active');
        });
    }
});

// ========== 富文本编辑器 ==========
function formatDoc(cmd, value = null) {
    document.execCommand(cmd, false, value);
    document.getElementById('instruction-editor').focus();
}

function createLink() {
    const url = prompt('请输入链接地址：', 'https://');
    if (url) formatDoc('createLink', url);
}

// ========== 会话管理 ==========
async function switchSession(sessionId) {
    const res = await fetch(`/api/sessions/${sessionId}/activate`, { method: 'POST' });
    if (res.ok) {
        location.reload();
    } else {
        alert('切换失败');
    }
}

function showCreateSession() {
    document.getElementById('session-modal').style.display = 'flex';
}

document.getElementById('create-session-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const form = e.target;
    const data = {
        name: form.name.value,
        quarter: form.quarter.value
    };
    const res = await fetch('/api/sessions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
    });
    if (res.ok) {
        location.reload();
    } else {
        alert('创建失败');
    }
});

// ========== 考核人 ==========
document.getElementById('add-assessor-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const form = e.target;
    const data = {
        name: form.name.value,
        employee_id: form.employee_id.value
    };
    const res = await fetch('/api/assessors', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
    });
    if (res.ok) {
        form.reset();
        alert('添加成功！');
        location.reload();
    } else {
        const err = await res.json().catch(() => ({}));
        alert('添加失败：' + (err.error || err.detail || ''));
    }
});

async function deleteAssessor(id) {
    if (!confirm('确定要删除该考核人吗？')) return;
    const res = await fetch(`/api/assessors/${id}`, { method: 'DELETE' });
    if (res.ok) location.reload();
}

// ========== 被考核人 ==========
document.getElementById('add-evaluatee-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const form = e.target;
    const data = {
        name: form.name.value,
        employee_id: form.employee_id.value,
        department: form.department.value || null,
        position: form.position.value || null,
        group_type: form.group_type.value || null,
        department_ref_score: form.department_ref_score.value || null
    };
    const res = await fetch('/api/evaluatees', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
    });
    if (res.ok) {
        form.reset();
        alert('添加成功！');
        location.reload();
    } else {
        const err = await res.json().catch(() => ({}));
        alert('添加失败：' + (err.error || err.detail || ''));
    }
});

async function deleteEvaluatee(id) {
    if (!confirm('确定要删除该被考核人吗？')) return;
    const res = await fetch(`/api/evaluatees/${id}`, { method: 'DELETE' });
    if (res.ok) location.reload();
}

// ========== 考核关系 ==========
document.getElementById('add-relation-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const form = e.target;
    const data = {
        assessor_id: parseInt(form.assessor_id.value),
        evaluatee_id: parseInt(form.evaluatee_id.value),
        weight: parseFloat(form.weight.value) || 1.0
    };
    const res = await fetch('/api/relations', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
    });
    if (res.ok) {
        form.reset();
        alert('添加成功！');
        location.reload();
    } else {
        alert('添加失败');
    }
});

async function deleteRelation(id) {
    if (!confirm('确定要删除该考核关系吗？')) return;
    const res = await fetch(`/api/relations/${id}`, { method: 'DELETE' });
    if (res.ok) location.reload();
}

// ========== 指标 ==========
document.getElementById('add-indicator-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const form = e.target;
    const data = {
        name: form.name.value,
        weight: parseFloat(form.weight.value) / 100,
        max_score: parseFloat(form.max_score.value) || 100
    };
    const res = await fetch('/api/indicators', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
    });
    if (res.ok) {
        form.reset();
        alert('添加成功！');
        location.reload();
    } else {
        alert('添加失败');
    }
});

async function deleteIndicator(id) {
    if (!confirm('确定要删除该指标吗？')) return;
    const res = await fetch(`/api/indicators/${id}`, { method: 'DELETE' });
    if (res.ok) location.reload();
}

// ========== Excel 导入 ==========
async function importAssessors(input) {
    const file = input.files[0];
    if (!file) return;
    const formData = new FormData();
    formData.append('file', file);
    const res = await fetch('/api/import/assessors', { method: 'POST', body: formData });
    const data = await res.json();
    if (res.ok && data.success) {
        const r = data.result;
        alert(`导入完成！新增 ${r.created} 人，更新 ${r.updated} 人，共 ${r.total} 条记录。`);
        location.reload();
    } else {
        alert('导入失败：' + (data.detail || data.error || '请检查文件格式'));
    }
    input.value = '';
}

async function importEvaluatees(input) {
    const file = input.files[0];
    if (!file) return;
    const formData = new FormData();
    formData.append('file', file);
    const res = await fetch('/api/import/evaluatees', { method: 'POST', body: formData });
    const data = await res.json();
    if (res.ok && data.success) {
        const r = data.result;
        alert(`导入完成！新增 ${r.created} 人，更新 ${r.updated} 人，共 ${r.total} 条记录。`);
        location.reload();
    } else {
        alert('导入失败：' + (data.detail || data.error || '请检查文件格式'));
    }
    input.value = '';
}

async function importRelations(input) {
    const file = input.files[0];
    if (!file) return;
    const formData = new FormData();
    formData.append('file', file);
    const res = await fetch('/api/import/relations', { method: 'POST', body: formData });
    const data = await res.json();
    if (res.ok && data.success) {
        const r = data.result;
        let msg = `导入完成！新增 ${r.created} 条关系，共 ${r.total} 条记录。`;
        if (r.errors && r.errors.length > 0) {
            msg += `\n\n以下记录导入失败：\n${r.errors.join('\n')}`;
        }
        alert(msg);
        location.reload();
    } else {
        alert('导入失败：' + (data.detail || data.error || '请检查文件格式'));
    }
    input.value = '';
}

// ========== 设置 ==========
async function saveSettings() {
    const editor = document.getElementById('instruction-editor');
    const html = editor.innerHTML.trim();
    const res = await fetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ instruction: html })
    });
    if (res.ok) {
        alert('设置已保存！');
    } else {
        const data = await res.json().catch(() => ({}));
        alert('保存失败：' + (data.error || ''));
    }
}

// ========== 修改密码 ==========
document.getElementById('change-password-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const form = e.target;
    if (form.new_password.value !== form.confirm_password.value) {
        alert('两次输入的新密码不一致');
        return;
    }
    const res = await fetch('/api/change-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            old_password: form.old_password.value,
            new_password: form.new_password.value
        })
    });
    const data = await res.json();
    if (res.ok && data.success) {
        alert('密码修改成功！');
        form.reset();
    } else {
        alert(data.message || '修改失败');
    }
});
