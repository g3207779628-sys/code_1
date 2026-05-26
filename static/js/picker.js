/**
 * Picker 通用工具：点击"选择"按钮 → 跳到独立 picker 页 → 选完返回原页面 + 自动回填
 *
 * 用法：
 *   <input type="hidden" name="sku_id[]" id="sku-0">
 *   <span class="picker-label" id="sku-0-label">未选择</span>
 *   <button onclick="openPicker('sku', 'sku-0')">选商品</button>
 *
 * 支持参数：
 *   - multi=1 多选模式（picker 页面带 checkbox）
 *   - warehouse_id=N 联动过滤（库位 picker 用）
 */

function _stateKey() {
    return 'picker_state_' + window.location.pathname;
}

function openPicker(type, targetId, opts) {
    opts = opts || {};
    // 1. 保存当前 form 状态到 sessionStorage
    const state = {};
    document.querySelectorAll('form').forEach(form => {
        form.querySelectorAll('input, select, textarea').forEach(el => {
            if (!el.name) return;
            if (el.type === 'file') return;
            const key = el.name + (el.id ? '#' + el.id : '');
            if (el.type === 'checkbox' || el.type === 'radio') {
                state[key + '__' + el.value] = el.checked;
            } else {
                state[key] = el.value;
            }
        });
    });
    // 同时存所有 picker-label 的文本（这些不在 form 里）
    document.querySelectorAll('.picker-label').forEach(el => {
        if (el.id) state['__label__' + el.id] = el.textContent;
    });
    sessionStorage.setItem(_stateKey(), JSON.stringify({state, target: targetId}));

    // 2. 跳 picker
    const ret = window.location.pathname + window.location.search;
    const params = new URLSearchParams();
    params.set('return', ret);
    params.set('target', targetId);
    Object.entries(opts).forEach(([k, v]) => params.set(k, v));
    window.location = '/picker/' + type + '?' + params.toString();
}

function restorePickerState() {
    const u = new URL(window.location);
    const pickerTarget = u.searchParams.get('picker_target');
    const pickerId = u.searchParams.get('picker_id');
    const pickerLabel = decodeURIComponent(u.searchParams.get('picker_label') || '');

    // v22: 无论用户在 picker 选了还是点"取消返回"都要恢复原表单字段。
    // 判断"从 picker 回来"用 sessionStorage 是否存在 picker_state（openPicker 时埋的标记）。
    const savedRaw = sessionStorage.getItem(_stateKey());
    if (!savedRaw) return;  // 首次进入页面，不是从 picker 回来
    const saved = JSON.parse(savedRaw);
    if (saved.state) {
        Object.entries(saved.state).forEach(([key, val]) => {
            if (key.startsWith('__label__')) {
                const id = key.substring(9);
                const el = document.getElementById(id);
                if (el) el.textContent = val;
                return;
            }
            const isCheckable = key.includes('__');
            const nameWithId = isCheckable ? key.split('__')[0] : key;
            const [name, id] = nameWithId.includes('#') ? nameWithId.split('#') : [nameWithId, null];
            let el;
            if (id) el = document.getElementById(id);
            if (!el && name) el = document.querySelector(`[name="${name}"]`);
            if (!el) return;
            if (isCheckable) {
                const val2 = key.split('__')[1];
                const cb = document.querySelector(`[name="${name}"][value="${val2}"]`);
                if (cb) cb.checked = val;
            } else {
                el.value = val;
            }
        });
    }

    // 把 picker 结果填到 target（仅当用户在 picker 选了 → 带 picker_target + picker_id 回来）
    if (pickerTarget && pickerId !== null) {
        const target = document.getElementById(pickerTarget);
        if (target) {
            target.value = pickerId;
            target.dispatchEvent(new Event('change', {bubbles: true}));
        }
        const labelEl = document.getElementById(pickerTarget + '-label');
        if (labelEl && pickerLabel) labelEl.textContent = pickerLabel;
    }

    // 清场
    sessionStorage.removeItem(_stateKey());
    history.replaceState({}, '', window.location.pathname + window.location.search.replace(/[?&]picker_[^&]+/g, '').replace(/^&/, '?'));
}

window.addEventListener('DOMContentLoaded', restorePickerState);
