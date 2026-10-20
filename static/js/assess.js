// 中层绩效考核系统 - 打分页面 JS
// 数据从 assess.html 中的 <script> 块传入：ASSESSOR_ID, RELATIONS, INDICATORS, SCORE_MAP, COMPLETED

let currentIndex = -1;

// 显示被考核人打分界面（从列表点击触发）
function showEvaluatee(evaluateeId) {
    currentIndex = RELATIONS.findIndex(r => r.evaluatee_id === evaluateeId);
    showEvaluateeDirect(evaluateeId);
}

// 实际显示打分界面（可从列表或"下一个"按钮触发）
function showEvaluateeDirect(evaluateeId) {
    const rel = RELATIONS.find(r => r.evaluatee_id === evaluateeId);
    if (!rel) return;

    document.getElementById('evaluatee-list-card').style.display = 'none';
    document.getElementById('score-area').style.display = 'block';

    document.getElementById('score-title').textContent = '为 ' + rel.evaluatee_name + ' 评分';
    document.getElementById('current-evaluatee-name').textContent = rel.evaluatee_name;
    document.getElementById('current-evaluatee-info').textContent =
        (rel.department || '未知部门');
    document.getElementById('evaluatee-id').value = evaluateeId;

    // 显示部门参考分（文本格式）
    const refScore = rel.department_ref_score;
    document.getElementById('dept-ref-score-value').textContent =
        (refScore !== null && refScore !== undefined && refScore !== '') ? refScore : '-';

    // 更新"下一个"按钮文案
    const nextBtn = document.querySelector('.btn-next');
    if (currentIndex >= 0 && currentIndex < RELATIONS.length - 1) {
        nextBtn.textContent = '下一个';
    } else {
        nextBtn.textContent = '完成并返回列表';
    }

    // 预填已有分数
    INDICATORS.forEach(ind => {
        const input = document.getElementById('score-' + ind.id);
        if (!input) return;
        const evalScores = SCORE_MAP[String(evaluateeId)] || SCORE_MAP[evaluateeId] || {};
        const val = evalScores[String(ind.id)] || evalScores[ind.id];
        if (val !== undefined && val !== null) {
            input.value = val;
        } else {
            input.value = '';
        }
    });

    updateTotal();
    window.scrollTo({ top: 0, behavior: 'smooth' });
}

// 隐藏打分区域，返回列表
function hideScoreArea() {
    document.getElementById('evaluatee-list-card').style.display = 'block';
    document.getElementById('score-area').style.display = 'none';
    currentIndex = -1;
    window.scrollTo({ top: 0, behavior: 'smooth' });
}

// 更新加权总分预览
function updateTotal() {
    let total = 0;
    INDICATORS.forEach(ind => {
        const input = document.getElementById('score-' + ind.id);
        if (input && input.value) {
            const score = parseFloat(input.value);
            if (!isNaN(score)) {
                total += score * ind.weight;
            }
        }
    });
    document.getElementById('total-score').textContent = total.toFixed(2);
}

// 暂存当前被考核人的分数
async function saveCurrentScores(silent = false) {
    const evaluateeId = parseInt(document.getElementById('evaluatee-id').value);
    if (!evaluateeId) {
        if (!silent) alert('请先选择被考核人');
        return false;
    }

    const scores = [];
    for (const ind of INDICATORS) {
        const input = document.getElementById('score-' + ind.id);
        if (input && input.value !== '') {
            const val = parseFloat(input.value);
            if (isNaN(val)) {
                if (!silent) alert('指标 "' + ind.name + '" 的分数格式不正确');
                return false;
            }
            if (val < 0 || val > 100) {
                if (!silent) alert('指标 "' + ind.name + '" 的分数应在 0-100 之间');
                return false;
            }
            scores.push({ indicator_id: ind.id, score: val });
        }
    }

    if (scores.length === 0) {
        if (!silent) alert('请至少填写一项分数后再暂存');
        return false;
    }

    try {
        const res = await fetch('/api/scores/' + ASSESSOR_ID + '/batch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-Access-Token': ACCESS_TOKEN },
            body: JSON.stringify({
                evaluatee_id: evaluateeId,
                scores: scores
            })
        });

        if (res.ok) {
            if (!SCORE_MAP[evaluateeId]) SCORE_MAP[evaluateeId] = {};
            scores.forEach(s => {
                SCORE_MAP[evaluateeId][s.indicator_id] = s.score;
            });
            if (!silent) alert('分数已暂存！退出后重新扫码可继续填写。');
            updateEvaluateeStatus(evaluateeId);
            return true;
        } else {
            const data = await res.json().catch(() => ({}));
            if (!silent) alert('保存失败：' + (data.detail || data.message || '请重试'));
            return false;
        }
    } catch (err) {
        if (!silent) alert('网络错误，请重试');
        return false;
    }
}

// "下一个"按钮：先保存当前，再显示下一个被考核人
async function nextEvaluatee() {
    const saved = await saveCurrentScores(true);
    if (currentIndex >= 0 && currentIndex < RELATIONS.length - 1) {
        currentIndex++;
        showEvaluateeDirect(RELATIONS[currentIndex].evaluatee_id);
    } else {
        // 最后一个，返回列表
        hideScoreArea();
    }
}

// 更新被考核人列表中的状态标签
function updateEvaluateeStatus(evaluateeId) {
    const statusDiv = document.getElementById('status-' + evaluateeId);
    if (!statusDiv) return;

    const evalScores = SCORE_MAP[evaluateeId] || {};
    const hasScores = Object.keys(evalScores).length > 0;

    let total = 0;
    INDICATORS.forEach(ind => {
        const val = evalScores[ind.id];
        if (val !== undefined && val !== null) {
            total += val * ind.weight;
        }
    });

    if (hasScores) {
        statusDiv.innerHTML =
            '<span class="badge badge-success">已评分</span>' +
            '<span class="evaluatee-total">当前总分: ' + total.toFixed(2) + '</span>';
    } else {
        statusDiv.innerHTML = '<span class="badge badge-pending">待评分</span>';
    }
}

// 提交全部考核
async function submitAllScores() {
    // 先保存当前正在编辑的分数（如果有）
    const scoreArea = document.getElementById('score-area');
    if (scoreArea && scoreArea.style.display !== 'none') {
        const evalId = document.getElementById('evaluatee-id').value;
        if (evalId) {
            const scores = [];
            for (const ind of INDICATORS) {
                const input = document.getElementById('score-' + ind.id);
                if (input && input.value !== '') {
                    const val = parseFloat(input.value);
                    if (isNaN(val) || val < 0 || val > 100) {
                        alert('当前编辑中存在超出 0-100 范围的分数，请修正后再提交');
                        return;
                    }
                    scores.push({ indicator_id: ind.id, score: val });
                }
            }
            if (scores.length > 0) {
                const res = await fetch('/api/scores/' + ASSESSOR_ID + '/batch', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-Access-Token': ACCESS_TOKEN },
                    body: JSON.stringify({ evaluatee_id: parseInt(evalId), scores: scores })
                });
                if (!res.ok) {
                    const data = await res.json().catch(() => ({}));
                    alert('保存失败：' + (data.detail || data.message || '请重试'));
                    return;
                }
                if (!SCORE_MAP[evalId]) SCORE_MAP[evalId] = {};
                scores.forEach(s => { SCORE_MAP[evalId][s.indicator_id] = s.score; });
            }
        }
    }

    // 检查是否所有被考核人都有所有指标的分数
    const missingList = [];
    for (const rel of RELATIONS) {
        const eid = rel.evaluatee_id;
        const evalScores = SCORE_MAP[eid] || {};
        for (const ind of INDICATORS) {
            if (evalScores[ind.id] === undefined || evalScores[ind.id] === null) {
                missingList.push(rel.evaluatee_name + '(' + ind.name + ')');
            }
        }
    }

    if (missingList.length > 0) {
        alert('以下人员尚未完成评分，无法提交：\n' + missingList.join('\n'));
        return;
    }

    if (!confirm('确认提交全部考核结果？提交后不可修改。')) return;

    try {
        const res = await fetch('/api/scores/' + ASSESSOR_ID + '/submit', {
            method: 'POST',
            headers: { 'X-Access-Token': ACCESS_TOKEN }
        });

        const data = await res.json().catch(() => ({}));
        if (res.ok && data.success !== false) {
            alert(data.message || '考核提交成功！');
            location.reload();
        } else {
            alert('提交失败：' + (data.message || data.detail || '请重试'));
        }
    } catch (err) {
        alert('网络错误，请重试');
    }
}

// 页面加载时
document.addEventListener('DOMContentLoaded', () => {
    RELATIONS.forEach(rel => {
        updateEvaluateeStatus(rel.evaluatee_id);
    });
});
