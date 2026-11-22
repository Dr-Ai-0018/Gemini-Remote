// Settings / status panel, shared by the floating button on Gemini pages and
// the toolbar popup. Pure DOM (textContent only, no HTML from data); every
// action goes through `api`, which talks to the background service worker.
(function (root) {
  'use strict';

  const ERRORS = {
    code_format: '请输入 6 位数字配对码',
    bad_code: '配对码不对，请核对终端里显示的数字',
    no_code: '配对码已过期或已用过。请在电脑上运行 gemini-remote pair 生成新的',
    relay_unreachable: '连不上 Relay。请确认电脑上已运行 gemini-remote relay，且端口一致',
    busy: '正在配对，请稍候',
    bad_origin: 'Relay 拒绝了这个扩展',
    port_invalid: '端口要在 1–65535 之间',
    unauthorized: 'Relay 不再认这个扩展，请重新配对',
  };

  const PAGE_KIND = { new_chat: '新对话页', conversation: '对话页' };

  const STYLE = `
    :host { all: initial; }
    .card { box-sizing: border-box; width: 300px; padding: 14px 16px; border-radius: 14px;
      font: 13px/1.5 system-ui, -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
      color: #1f1f1f; background: #ffffff; box-shadow: 0 6px 24px rgba(0,0,0,.18); border: 1px solid #e3e3e3; }
    .head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
    .title { font-weight: 600; font-size: 14px; }
    .ver { color: #6b6b6b; font-size: 12px; margin-left: 6px; font-weight: 400; }
    .row { display: flex; align-items: flex-start; gap: 8px; margin: 6px 0; }
    .dot { flex: none; width: 8px; height: 8px; border-radius: 50%; margin-top: 6px; background: #9e9e9e; }
    .ok .dot { background: #1e8e3e; } .wait .dot { background: #f29900; } .bad .dot { background: #d93025; }
    .muted { color: #6b6b6b; font-size: 12px; }
    .warn { color: #b06000; font-size: 12px; }
    .err { color: #d93025; font-size: 12px; min-height: 0; }
    .section { margin-top: 10px; padding-top: 10px; border-top: 1px solid #ececec; }
    .line { display: flex; gap: 6px; margin-top: 6px; }
    input { box-sizing: border-box; flex: 1; min-width: 0; padding: 6px 8px; border: 1px solid #c7c7c7;
      border-radius: 8px; font: inherit; color: inherit; background: transparent; }
    input.code { letter-spacing: 4px; font-variant-numeric: tabular-nums; }
    button { padding: 6px 12px; border: 1px solid #c7c7c7; border-radius: 8px; font: inherit;
      color: inherit; background: transparent; cursor: pointer; }
    button.primary { background: #0b57d0; border-color: #0b57d0; color: #fff; }
    button.link { border: none; padding: 0; color: #0b57d0; background: none; font-size: 12px; }
    button.x { border: none; padding: 0 4px; font-size: 18px; line-height: 1; color: #6b6b6b; }
    button:disabled { opacity: .5; cursor: default; }
    [hidden] { display: none !important; }
    @media (prefers-color-scheme: dark) {
      .card { color: #e3e3e3; background: #1f1f1f; border-color: #3c3c3c; }
      .muted, .ver, button.x { color: #a8a8a8; } .section { border-color: #333; }
      input, button { border-color: #555; } button.link { color: #a8c7fa; }
      button.primary { background: #a8c7fa; border-color: #a8c7fa; color: #062e6f; }
    }`;

  function el(doc, tag, props = {}, children = []) {
    const node = doc.createElement(tag);
    for (const [key, value] of Object.entries(props)) {
      if (key === 'class') node.className = value;
      else if (key === 'text') node.textContent = value;
      else node.setAttribute(key, value);
    }
    for (const child of children) node.append(child);
    return node;
  }

  function relayLine(state) {
    switch (state.relay) {
      case 'connected':
        return ['ok', `已连接 Relay ${state.relayVersion || ''}`.trim()];
      case 'connecting':
        return ['wait', '正在连接 Relay…'];
      case 'offline': {
        const wait = state.retryAt ? Math.max(0, Math.round((state.retryAt - Date.now()) / 1000)) : null;
        return ['bad', `连不上 Relay（127.0.0.1:${state.port}）${wait !== null ? `，${wait} 秒后重试` : ''}`];
      }
      case 'unauthorized':
        return ['bad', '配对已失效，请重新配对'];
      default:
        return ['wait', '还没和 Relay 配对'];
    }
  }

  // Builds the panel once; update(state, page) refreshes it without touching
  // what the user is typing. `page` is this tab's preflight (null in the popup).
  function createPanel(doc, api, { onClose } = {}) {
    const ver = el(doc, 'span', { class: 'ver' });
    const close = el(doc, 'button', { class: 'x', title: '收起', 'aria-label': '收起', text: '×' });
    close.hidden = !onClose;
    if (onClose) close.addEventListener('click', onClose);

    const relayText = el(doc, 'div');
    const retry = el(doc, 'button', { class: 'link', text: '立即重试' });
    const relayRow = el(doc, 'div', { class: 'row' }, [el(doc, 'span', { class: 'dot' }),
      el(doc, 'div', {}, [relayText, retry])]);
    const versionWarn = el(doc, 'div', { class: 'warn' });
    const pageText = el(doc, 'div');
    const pageRow = el(doc, 'div', { class: 'row' }, [el(doc, 'span', { class: 'dot' }), pageText]);
    const tasks = el(doc, 'div', { class: 'muted', text: '执行任务：下一版本开放（目前只建立连接）' });

    const codeInput = el(doc, 'input', { class: 'code', inputmode: 'numeric', maxlength: '7',
      placeholder: '6 位配对码', autocomplete: 'off', 'aria-label': '配对码' });
    const pairButton = el(doc, 'button', { class: 'primary', text: '配对' });
    const pairError = el(doc, 'div', { class: 'err' });
    const pairSection = el(doc, 'div', { class: 'section' }, [
      el(doc, 'div', { text: '配对' }),
      el(doc, 'div', { class: 'muted', text: 'Relay 启动时终端会显示 6 位配对码（5 分钟有效）；过期了在电脑上运行 gemini-remote pair。' }),
      el(doc, 'div', { class: 'line' }, [codeInput, pairButton]),
      pairError,
    ]);

    const unpairButton = el(doc, 'button', { class: 'link', text: '解除配对' });
    const repairButton = el(doc, 'button', { class: 'link', text: '重新配对' });
    const pairedSection = el(doc, 'div', { class: 'section line' }, [repairButton, unpairButton]);

    const portInput = el(doc, 'input', { type: 'number', min: '1', max: '65535', 'aria-label': 'Relay 端口' });
    const portButton = el(doc, 'button', { text: '保存' });
    const portError = el(doc, 'div', { class: 'err' });
    const portSection = el(doc, 'div', { class: 'section' }, [
      el(doc, 'div', { text: 'Relay 端口' }),
      el(doc, 'div', { class: 'line' }, [portInput, portButton]),
      portError,
    ]);

    const card = el(doc, 'div', { class: 'card', role: 'dialog', 'aria-label': 'Gemini Remote' }, [
      el(doc, 'div', { class: 'head' }, [el(doc, 'div', { class: 'title', text: 'Gemini Remote' }, [ver]), close]),
      relayRow, versionWarn, pageRow, tasks, pairSection, pairedSection, portSection,
    ]);

    let current = null;
    let showPair = false;
    let unpairArmed = null;

    function apply(result) {
      if (result && result.state) update(result.state);
      return result || { ok: false };
    }

    async function submitPair() {
      pairError.textContent = '';
      pairButton.disabled = true;
      try {
        const result = apply(await api.pair(codeInput.value));
        if (result.ok) {
          codeInput.value = '';
          showPair = false;
          if (current) update(current);
        } else {
          pairError.textContent = ERRORS[result.error] || `配对失败（${result.error}）`;
        }
      } finally {
        pairButton.disabled = false;
      }
    }

    pairButton.addEventListener('click', submitPair);
    codeInput.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') submitPair();
    });
    retry.addEventListener('click', async () => apply(await api.reconnect()));
    repairButton.addEventListener('click', () => {
      showPair = !showPair;
      if (current) update(current);
      if (showPair) codeInput.focus();
    });
    unpairButton.addEventListener('click', async () => {
      if (!unpairArmed) { // two clicks, so a stray click does not unpair
        unpairButton.textContent = '确认解除？';
        unpairArmed = setTimeout(() => {
          unpairArmed = null;
          unpairButton.textContent = '解除配对';
        }, 3000);
        return;
      }
      clearTimeout(unpairArmed);
      unpairArmed = null;
      unpairButton.textContent = '解除配对';
      apply(await api.unpair());
    });
    portButton.addEventListener('click', async () => {
      portError.textContent = '';
      const result = apply(await api.setPort(Number(portInput.value)));
      if (!result.ok) portError.textContent = ERRORS[result.error] || `保存失败（${result.error}）`;
    });

    let page = null;

    function update(state, pageResult) {
      current = state;
      if (pageResult !== undefined) page = pageResult;
      ver.textContent = state.extensionVersion ? `v${state.extensionVersion}` : '';

      const [level, text] = relayLine(state);
      relayRow.className = `row ${level}`;
      relayText.textContent = text;
      retry.hidden = state.relay !== 'offline';
      const mismatch = state.relayVersion && state.extensionVersion && state.relayVersion !== state.extensionVersion;
      versionWarn.hidden = !mismatch;
      versionWarn.textContent = mismatch
        ? `扩展 ${state.extensionVersion} 和 Relay ${state.relayVersion} 版本不一致：更新代码后请重启 Relay，并重新构建、重新加载扩展`
        : '';

      if (page) {
        pageRow.className = `row ${page.ok ? 'ok' : page.pending ? 'wait' : 'bad'}`;
        const kind = PAGE_KIND[page.page] || page.page;
        pageText.textContent = page.ok ? `本页（${kind}）结构检查通过`
          : page.pending ? `本页（${kind}）正在检查页面结构…`
            : `本页（${kind}）结构检查未通过：${(page.problems || []).map((p) => p.name).join('、')}`;
      } else {
        const tabs = state.tabs || { open: 0, ready: 0 };
        pageRow.className = `row ${tabs.ready ? 'ok' : 'wait'}`;
        pageText.textContent = tabs.open ? `Gemini 标签页：打开 ${tabs.open} 个，${tabs.ready} 个结构检查通过`
          : '没有打开的 Gemini 标签页';
      }
      tasks.hidden = Boolean(state.tasksEnabled);

      const needsPair = !state.paired || state.relay === 'unauthorized';
      pairSection.hidden = !(needsPair || showPair);
      pairedSection.hidden = !state.paired;
      repairButton.textContent = showPair && !needsPair ? '取消' : '重新配对';
      repairButton.hidden = needsPair;
      if (portInput.getRootNode().activeElement !== portInput && !portError.textContent) portInput.value = String(state.port);
    }

    return { element: card, update, style: STYLE };
  }

  root.GeminiRemotePanel = Object.freeze({ createPanel, ERRORS, STYLE });
})(globalThis);
