/* AI 现场拍摄指导助手 · 前端逻辑
 * 纯原生 JS：无构建、无 CDN、无第三方依赖，离线可用。
 * 契约：docs/INTERFACES.md §4（HTTP API）与 §5（前端行为）。
 *
 * 设计要点：
 * 1) 所有请求走同源相对路径（/api/...、/static/...、/output/...），不硬编码主机名。
 * 2) 服务未启动 / 无相机 / 接口字段缺省，都必须优雅降级：页面不白屏、不抛未捕获错误。
 * 3) 建议卡片只讲“动作 + 理由”，评分/特征/耗时默认隐藏，勾选「调试」才显示。
 */
(function () {
  'use strict';

  /* ============================ 常量 ============================ */

  var BUSY_TEXT = '处理中…';
  var MAX_EDGE = 1280;            // 抓帧长边上限（契约 §4 大图传输）
  var JPEG_QUALITY = 0.7;         // 契约：canvas 出图质量 0.7
  var SUGGEST_TIMEOUT = 3000;     // 契约 §5.3：3 秒超时
  var MIRROR_PREVIEW = false;     // 前摄镜像默认关闭：镜像会让叠加坐标与服务端归一化坐标不一致
  var DEFAULT_INTENT = '电影感人物特写，背景虚化';
  var ORANGE = '#E8752C';
  var HEALTH_RETRY_MS = 10000;
  var CAMERA_SOFT_TIMEOUT = 8000; // 相机软超时：权限弹窗被忽略时也要能落到演示模式

  var INTENT_LABELS = {
    portrait_closeup: '人像特写',
    portrait_medium: '人像中景',
    landscape: '风光',
    product: '产品',
    emotional: '情绪'
  };

  /* ============================ 状态 ============================ */

  var state = {
    sessionId: '',
    apiOk: false,
    health: null,
    mode: 'init',              // init | camera | demo
    stream: null,
    scenarios: [],
    scenario: null,
    demoImage: null,
    overlay: null,             // 最近一次 /api/suggest 返回的 overlay（可为 null）
    lastSuggestion: null,      // 最近一次 /api/suggest 完整响应
    lastScore: null,           // 最近 current_score.total
    lastIntentText: '',
    intentId: 'portrait_medium',
    beforeDataUrl: null,       // 上一次给建议时的抓帧
    followedAdvice: false,     // 是否点过「已按此调整」/「有用」
    adoptedActionIds: [],      // 采纳过的动作 ID（数组，兼容旧浏览器）
    suggestFailures: 0,
    shotStartTs: 0,
    healthRetryTimer: null,
    toastTimer: null,
    toastHideTimer: null,
    compareModal: null,
    lastError: null
  };

  /* ============================ 小工具 ============================ */

  function $(id) {
    return document.getElementById(id);
  }

  function isNum(v) {
    return typeof v === 'number' && isFinite(v);
  }

  function fmtNum(v, digits) {
    if (!isNum(v)) return '—';
    return v.toFixed(digits === undefined ? 2 : digits);
  }

  function fmtSec(sec) {
    if (!isNum(sec)) return '—';
    return (Math.round(sec * 10) / 10).toFixed(1) + ' 秒';
  }

  function fmtText(v) {
    if (v === null || v === undefined || v === '') return '—';
    return String(v);
  }

  function addUnique(list, value) {
    if (!value) return;
    for (var i = 0; i < list.length; i++) {
      if (list[i] === value) return;
    }
    list.push(value);
  }

  function makeSessionId() {
    var id = '';
    try {
      if (window.crypto && typeof window.crypto.randomUUID === 'function') {
        id = window.crypto.randomUUID();
      }
    } catch (err) { id = ''; }
    if (!id) {
      id = 'ts-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10);
    }
    return 'web-' + id;
  }

  function makeApiError(message, status, payload) {
    var err = new Error(message);
    err.name = 'ApiError';
    err.status = status || 0;
    err.payload = payload || null;
    return err;
  }

  /* ============================ 顶部 toast ============================ */

  function toast(message, kind) {
    var el = $('toast');
    if (!el) return;
    if (state.toastTimer) clearTimeout(state.toastTimer);
    if (state.toastHideTimer) clearTimeout(state.toastHideTimer);
    el.textContent = message;
    el.className = 'toast toast--' + (kind || 'info') + ' toast--show';
    el.hidden = false;
    state.toastTimer = setTimeout(function () {
      el.className = 'toast toast--' + (kind || 'info');
      state.toastHideTimer = setTimeout(function () {
        el.hidden = true;
      }, 220);
      state.toastTimer = null;
    }, 3000);
  }

  /* ============================ 状态徽标 ============================ */

  function setStatus(text, kind) {
    var el = $('status');
    if (!el) return;
    el.textContent = text;
    el.className = 'status status--' + (kind || 'init');
  }

  function setModeStatus() {
    if (!state.apiOk) {
      setStatus('服务未启动', 'error');
      return;
    }
    if (state.mode === 'camera') setStatus('相机就绪', 'ok');
    else if (state.mode === 'demo') setStatus('演示模式 · 无相机', 'demo');
    else setStatus('初始化…', 'init');
  }

  function setSuggestEnabled(enabled) {
    var btn = $('btn-suggest');
    if (!btn) return;
    btn.disabled = !enabled;
    btn.title = enabled ? '' : '服务未启动：先运行 scripts/start.ps1 再刷新页面';
  }

  /* ============================ 网络层 ============================ */

  /**
   * API 基址：默认空 = 同源（PWA / 本机浏览器）。
   * Capacitor 套壳或用 `?api=https://<局域网IP>:8443` 打开时，指向电脑上的服务。
   * 优先级：window.__LB_API_BASE__（构建时注入）> ?api= 查询参数（会记住）> localStorage。
   */
  var API_BASE = (function () {
    try {
      var injected = (typeof window.__LB_API_BASE__ === 'string') ? window.__LB_API_BASE__.trim() : '';
      if (injected) return injected.replace(/\/+$/, '');
      var params = (typeof URLSearchParams === 'function') ? new URLSearchParams(location.search) : null;
      var fromQuery = params ? (params.get('api') || '').trim() : '';
      if (fromQuery) {
        try { localStorage.setItem('lb_api_base', fromQuery); } catch (err) { /* ignore */ }
        return fromQuery.replace(/\/+$/, '');
      }
      var saved = '';
      try { saved = (localStorage.getItem('lb_api_base') || '').trim(); } catch (err) { saved = ''; }
      return saved.replace(/\/+$/, '');
    } catch (err) {
      return '';
    }
  })();

  /** 给同源相对路径加上 API 基址（已经是绝对 URL 的原样返回）。 */
  function apiUrl(path) {
    if (!path) return path;
    if (/^[a-z]+:\/\//i.test(path)) return path;
    if (!API_BASE) return path;
    return API_BASE + (path.charAt(0) === '/' ? path : '/' + path);
  }

  /**
   * 同源 JSON 请求：统一超时、统一错误文案、统一解析。
   * @param {string} path 相对路径，如 /api/suggest
   * @param {{method?:string, body?:object|null, timeout?:number}} options
   */
  function apiFetch(path, options) {
    options = options || {};
    var method = options.method || 'GET';
    var timeout = isNum(options.timeout) ? options.timeout : 0;
    var controller = null;
    var timer = null;
    var fetchOptions = {
      method: method,
      headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
      cache: 'no-store'
    };
    if (options.body !== undefined && options.body !== null) {
      fetchOptions.body = JSON.stringify(options.body);
    }
    if (typeof AbortController === 'function') {
      controller = new AbortController();
      fetchOptions.signal = controller.signal;
      if (timeout > 0) {
        timer = setTimeout(function () {
          try { controller.abort(); } catch (err) { /* ignore */ }
        }, timeout);
      }
    }
    return fetch(apiUrl(path), fetchOptions)
      .then(function (res) {
        return res.text().then(function (text) {
          var data = null;
          if (text) {
            try { data = JSON.parse(text); } catch (err) { data = null; }
          }
          if (!res.ok) {
            var detail = (data && (data.detail || data.message)) || ('HTTP ' + res.status);
            throw makeApiError(String(detail), res.status, data);
          }
          if (data === null) throw makeApiError('返回内容不是合法 JSON', res.status, null);
          return data;
        });
      })
      .catch(function (err) {
        if (controller && controller.signal && controller.signal.aborted) {
          throw makeApiError('请求超时（' + Math.round(timeout / 1000) + ' 秒没有响应）', 0, null);
        }
        if (err && err.name === 'ApiError') throw err;
        var reason = (err && err.message) ? err.message : '网络错误';
        throw makeApiError('连不上本地服务：' + reason, 0, null);
      })
      .then(function (data) {
        if (timer) clearTimeout(timer);
        return data;
      }, function (err) {
        if (timer) clearTimeout(timer);
        throw err;
      });
  }

  /** 请求期间禁用按钮并显示「处理中…」，结束后恢复原文案。 */
  function withBusy(btn, task) {
    if (!btn) return Promise.resolve().then(task);
    var oldText = btn.textContent;
    btn.disabled = true;
    btn.classList.add('is-busy');
    btn.textContent = BUSY_TEXT;
    return Promise.resolve()
      .then(task)
      .catch(function (err) {
        // 任务内部应自行 catch；这里兜底，避免未处理的 rejection
        state.lastError = err;
      })
      .then(function () {
        btn.textContent = oldText;
        btn.classList.remove('is-busy');
        btn.disabled = false;
      });
  }

  /* ============================ PWA（可安装 / 离线壳） ============================ */

  var deferredInstallPrompt = null;

  function isSecureContextNow() {
    try {
      return location.protocol === 'https:' ||
        location.hostname === 'localhost' || location.hostname === '127.0.0.1' ||
        location.protocol === 'capacitor:';
    } catch (err) {
      return false;
    }
  }

  function setupPwa() {
    var installBtn = $('btn-install');

    // 1) Service Worker：只在安全上下文注册（localhost / https / Capacitor 的 https://localhost）
    try {
      if (isSecureContextNow() && 'serviceWorker' in navigator) {
        navigator.serviceWorker.register('sw.js').then(function (reg) {
          reg.addEventListener('updatefound', function () {
            var worker = reg.installing;
            if (!worker) return;
            worker.addEventListener('statechange', function () {
              if (worker.state === 'installed' && navigator.serviceWorker.controller) {
                toast('有新版本：点状态栏重载即可', 'warn');
              }
            });
          });
        }).catch(function (err) { state.lastError = err; });
      }
    } catch (err) { state.lastError = err; }

    // 2) 安装到主屏幕
    window.addEventListener('beforeinstallprompt', function (event) {
      event.preventDefault();
      deferredInstallPrompt = event;
      if (installBtn) installBtn.hidden = false;
    });
    window.addEventListener('appinstalled', function () {
      deferredInstallPrompt = null;
      if (installBtn) installBtn.hidden = true;
      toast('已安装到主屏幕', 'ok');
    });
    if (installBtn) {
      installBtn.addEventListener('click', function () {
        if (!deferredInstallPrompt) {
          toast('这个浏览器不支持自动安装：用菜单里的「添加到主屏幕」', 'warn');
          return;
        }
        deferredInstallPrompt.prompt();
        var choice = deferredInstallPrompt.userChoice || Promise.resolve({ outcome: 'dismissed' });
        choice.then(function (result) {
          if (result && result.outcome === 'accepted') toast('正在安装…', 'ok');
          deferredInstallPrompt = null;
          installBtn.hidden = true;
        });
      });
    }
    var standalone = false;
    try {
      standalone = window.matchMedia('(display-mode: standalone)').matches ||
        window.navigator.standalone === true;
    } catch (err) { standalone = false; }
    if (standalone && installBtn) installBtn.hidden = true;

    // 3) 手机用 http://局域网IP 打开时，相机一定被浏览器禁用：明确告诉用户换 https
    if (!isSecureContextNow()) {
      setTimeout(function () {
        toast('当前 http 页面浏览器禁用相机：手机上请用 scripts/start-https.ps1 的 https 地址', 'warn');
      }, 1200);
    }
  }

  /* ============================ 启动流程 ============================ */

  function init() {
    state.sessionId = makeSessionId();
    state.shotStartTs = Date.now();
    bindEvents();
    ensureDownloadLinks();
    setupPwa();
    setStatus('初始化…', 'init');
    setSuggestEnabled(false);
    var hint = $('vf-hint');
    if (hint) hint.textContent = '正在启动相机…';

    startOverlayLoop();

    // 契约 §5.3：健康检查与 getUserMedia 并行。两者必须解耦——
    // 用户忽略权限弹窗时 getUserMedia 可能长时间不 settle，不能让整页卡在“初始化…”。
    checkHealth().then(function () {
      if (state.apiOk) onApiReady(false);
      else onApiDown(false);
      renderDebug();
    });
    startCameraOrDemo().then(function () {
      setModeStatus();
    });

    window.addEventListener('beforeunload', stopStream);
  }

  function checkHealth() {
    return apiFetch('/api/health', { method: 'GET', timeout: 5000 })
      .then(function (data) {
        state.health = data || null;
        state.apiOk = !(data && data.ok === false);
      })
      .catch(function () {
        state.health = null;
        state.apiOk = false;
      });
  }

  function onApiReady(isRetry) {
    setSuggestEnabled(true);
    if (state.healthRetryTimer) {
      clearTimeout(state.healthRetryTimer);
      state.healthRetryTimer = null;
    }
    setModeStatus();
    if (state.mode !== 'camera' && state.scenarios.length === 0) {
      loadDemoScenarios();
    }
    if (isRetry) {
      toast('服务已连接', 'ok');
      if (!isPanelHidden()) loadEdit();
    }
  }

  function onApiDown(isRetry) {
    setStatus('服务未启动', 'error');
    setSuggestEnabled(false);
    if (state.mode !== 'camera') {
      var strip = $('demo-strip');
      if (strip) strip.hidden = false;
      showDemoError('演示场景需要服务启动后才能加载（先运行 scripts/start.ps1 再刷新）。');
    }
    if (!isRetry) {
      toast('服务未启动：请先运行 scripts/start.ps1，页面会自动重试连接', 'error');
    }
    scheduleHealthRetry();
  }

  function scheduleHealthRetry() {
    if (state.healthRetryTimer || state.apiOk) return;
    state.healthRetryTimer = setTimeout(function () {
      state.healthRetryTimer = null;
      if (state.apiOk) return;
      checkHealth().then(function () {
        if (state.apiOk) onApiReady(true);
        else scheduleHealthRetry();
      });
    }, HEALTH_RETRY_MS);
  }

  function retryHealthNow() {
    if (state.apiOk) {
      toast('服务连接正常', 'ok');
      return;
    }
    setStatus('检测中…', 'init');
    checkHealth().then(function () {
      if (state.apiOk) onApiReady(true);
      else {
        setStatus('服务未启动', 'error');
        toast('还是连不上服务：确认 scripts/start.ps1 已启动且端口为 8000', 'error');
        scheduleHealthRetry();
      }
    });
  }

  /* ============================ 相机 / 演示模式 ============================ */

  function cameraErrorText(err) {
    var name = err && err.name;
    if (name === 'NotAllowedError' || name === 'SecurityError') return '相机权限被拒绝';
    if (name === 'NotFoundError' || name === 'DevicesNotFoundError') return '没有找到摄像头';
    if (name === 'NotReadableError' || name === 'TrackStartError') return '摄像头被其它程序占用';
    if (name === 'OverconstrainedError') return '相机不支持 1280x720';
    if (name === 'TypeError') return '当前环境不支持相机（需要 localhost 或 HTTPS）';
    return (err && err.message) ? err.message : '相机不可用';
  }

  function startCamera() {
    if (!navigator.mediaDevices || typeof navigator.mediaDevices.getUserMedia !== 'function') {
      return Promise.reject(new Error('浏览器不支持相机（需要 localhost 或 HTTPS）'));
    }
    return navigator.mediaDevices
      .getUserMedia({ video: { width: 1280, height: 720 }, audio: false })
      .then(function (stream) {
        state.stream = stream;
        var video = $('video');
        if (!video) throw new Error('页面缺少 video 元素');
        video.srcObject = stream;
        var playResult = null;
        try { playResult = video.play(); } catch (err) { playResult = null; }
        if (playResult && typeof playResult.catch === 'function') {
          playResult.catch(function () { /* 自动播放被拒绝时忽略，画面通常仍会显示 */ });
        }
        return waitForVideo(video);
      });
  }

  function waitForVideo(video) {
    return new Promise(function (resolve, reject) {
      if (video.videoWidth > 0) return resolve();
      var finished = false;
      var timer = setTimeout(function () {
        if (finished) return;
        finished = true;
        cleanup();
        reject(new Error('相机画面启动超时'));
      }, 7000);
      function onReady() {
        if (finished) return;
        if (video.videoWidth > 0) {
          finished = true;
          cleanup();
          resolve();
        }
      }
      function cleanup() {
        clearTimeout(timer);
        video.removeEventListener('loadeddata', onReady);
        video.removeEventListener('canplay', onReady);
        video.removeEventListener('playing', onReady);
      }
      video.addEventListener('loadeddata', onReady);
      video.addEventListener('canplay', onReady);
      video.addEventListener('playing', onReady);
      onReady();
    });
  }

  function stopStream() {
    if (state.stream) {
      try {
        var tracks = state.stream.getTracks();
        for (var i = 0; i < tracks.length; i++) tracks[i].stop();
      } catch (err) { /* ignore */ }
      state.stream = null;
    }
  }

  /**
   * 启动相机；拿不到就落到演示模式。
   * 加软超时：权限弹窗被忽略 / 无头环境里 getUserMedia 不 settle 时，
   * 超时后先进入演示模式；如果之后相机又成功，再切回相机模式。
   */
  function startCameraOrDemo() {
    var settled = false;
    var softTimer = setTimeout(function () {
      if (settled) return;
      settled = true;
      enterDemoMode(new Error('相机启动超时（可能是权限弹窗没点）'));
    }, CAMERA_SOFT_TIMEOUT);

    return startCamera().then(function () {
      settled = true;
      clearTimeout(softTimer);
      enterCameraMode();
    }, function (err) {
      clearTimeout(softTimer);
      if (settled) {
        // 软超时已经进了演示模式：这里不再重复切换
        return;
      }
      settled = true;
      enterDemoMode(err);
    });
  }

  function enterCameraMode() {
    state.mode = 'camera';
    var vf = $('viewfinder');
    if (vf) vf.classList.remove('is-demo');
    var hint = $('vf-hint');
    if (hint) hint.hidden = true;
    var mode = $('vf-mode');
    if (mode) mode.hidden = true;
    var strip = $('demo-strip');
    if (strip) strip.hidden = true;
    showDemoError('');
    setModeStatus();
  }

  function enterDemoMode(err) {
    state.mode = 'demo';
    stopStream();
    var vf = $('viewfinder');
    if (vf) vf.classList.add('is-demo');
    var hint = $('vf-hint');
    if (hint) {
      hint.hidden = false;
      hint.textContent = '演示模式：' + cameraErrorText(err);
    }
    var mode = $('vf-mode');
    if (mode) {
      mode.hidden = false;
      mode.textContent = '演示模式';
    }
    var strip = $('demo-strip');
    if (strip) strip.hidden = false;
    setModeStatus();
    if (state.apiOk) loadDemoScenarios();
  }

  /* ============================ 演示场景 ============================ */

  function showDemoError(message) {
    var el = $('demo-error');
    if (!el) return;
    if (!message) {
      el.textContent = '';
      el.hidden = true;
      return;
    }
    el.textContent = message;
    el.hidden = false;
  }

  function loadDemoScenarios() {
    showDemoError('');
    var box = $('demo-scenarios');
    if (box && !box.children.length) {
      box.textContent = '正在加载演示场景…';
    }
    return apiFetch('/api/demo', { method: 'GET', timeout: 8000 })
      .then(function (data) {
        var scenarios = (data && Array.isArray(data.scenarios)) ? data.scenarios : [];
        state.scenarios = scenarios;
        renderDemoScenarios();
        if (!scenarios.length) {
          showDemoError('服务没有返回演示场景（/api/demo 为空）。');
          return;
        }
        if (!state.scenario) selectScenario(scenarios[0].id, true);
      })
      .catch(function (err) {
        showDemoError('演示场景加载失败：' + err.message);
      });
  }

  function renderDemoScenarios() {
    var box = $('demo-scenarios');
    if (!box) return;
    box.textContent = '';
    if (!state.scenarios.length) return;
    var frag = document.createDocumentFragment();
    for (var i = 0; i < state.scenarios.length; i++) {
      var sc = state.scenarios[i] || {};
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'demo-thumb';
      btn.setAttribute('data-id', sc.id || '');
      btn.title = sc.label || sc.id || '演示场景';
      var img = document.createElement('img');
      img.src = apiUrl(sc.frame_url || '');
      img.alt = sc.label || sc.id || '演示场景';
      img.loading = 'lazy';
      var cap = document.createElement('span');
      cap.textContent = sc.label || sc.id || '演示场景';
      btn.appendChild(img);
      btn.appendChild(cap);
      frag.appendChild(btn);
    }
    box.appendChild(frag);
  }

  function selectScenario(id, silent) {
    var sc = null;
    for (var i = 0; i < state.scenarios.length; i++) {
      if (state.scenarios[i] && state.scenarios[i].id === id) {
        sc = state.scenarios[i];
        break;
      }
    }
    if (!sc) return;
    state.scenario = sc;

    var thumbs = $('demo-scenarios');
    if (thumbs) {
      var nodes = thumbs.querySelectorAll('.demo-thumb');
      for (var j = 0; j < nodes.length; j++) {
        if (nodes[j].getAttribute('data-id') === id) nodes[j].classList.add('is-selected');
        else nodes[j].classList.remove('is-selected');
      }
    }

    var hint = $('vf-hint');
    if (hint) {
      hint.hidden = false;
      hint.textContent = '正在加载演示画面…';
    }

    var url = apiUrl(sc.frame_url || '');
    loadImage(url)
      .then(function (img) {
        state.demoImage = img;
        if (hint) hint.hidden = true;
        drawOverlay();
        if (!silent) {
          var input = $('intent-input');
          if (input && sc.intent && (!input.value || input.value === DEFAULT_INTENT)) {
            input.value = sc.intent;
          }
        }
      })
      .catch(function () {
        state.demoImage = null;
        if (hint) {
          hint.hidden = false;
          hint.textContent = '演示画面加载失败：' + url;
        }
        showDemoError('演示图片加载失败：' + url);
      });
  }

  function loadImage(src) {
    return new Promise(function (resolve, reject) {
      if (!src) return reject(new Error('图片地址为空'));
      var img = new Image();
      img.onload = function () { resolve(img); };
      img.onerror = function () { reject(new Error('图片加载失败')); };
      img.src = src;
    });
  }

  /* ============================ 抓帧 ============================ */

  function drawSourceToDataUrl(source, srcW, srcH) {
    try {
      var scale = Math.min(1, MAX_EDGE / Math.max(srcW, srcH));
      var cw = Math.max(1, Math.round(srcW * scale));
      var ch = Math.max(1, Math.round(srcH * scale));
      var canvas = document.createElement('canvas');
      canvas.width = cw;
      canvas.height = ch;
      var ctx = canvas.getContext('2d');
      if (!ctx) return null;
      ctx.drawImage(source, 0, 0, cw, ch);
      return canvas.toDataURL('image/jpeg', JPEG_QUALITY);
    } catch (err) {
      state.lastError = err;
      return null;
    }
  }

  /** 抓当前画面 → dataURL（JPEG 0.7，长边 ≤1280）。相机模式取 video，演示模式取场景图。 */
  function captureFrame() {
    var video = $('video');
    if (state.mode === 'camera' && video && video.videoWidth > 0 && video.videoHeight > 0) {
      return drawSourceToDataUrl(video, video.videoWidth, video.videoHeight);
    }
    // 相机尚未 settle 时也允许用已选中的演示场景当作当前画面
    if (state.mode !== 'camera' && state.demoImage && state.demoImage.naturalWidth > 0) {
      return drawSourceToDataUrl(state.demoImage, state.demoImage.naturalWidth, state.demoImage.naturalHeight);
    }
    return null;
  }

  /* ============================ 取景叠加层 ============================ */

  function startOverlayLoop() {
    function frame() {
      try { drawOverlay(); } catch (err) { state.lastError = err; }
      window.requestAnimationFrame(frame);
    }
    window.requestAnimationFrame(frame);
  }

  function ensureCanvasSize(canvas) {
    var rect = canvas.getBoundingClientRect();
    var dpr = Math.min(2, window.devicePixelRatio || 1);
    var width = Math.max(1, Math.round(rect.width * dpr));
    var height = Math.max(1, Math.round(rect.height * dpr));
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
    return { w: rect.width, h: rect.height, dpr: dpr };
  }

  /** 等比 cover 贴合（与 CSS object-fit: cover 一致），返回目标容器内的绘制区域。 */
  function coverRect(srcW, srcH, dstW, dstH) {
    if (!srcW || !srcH) return { x: 0, y: 0, w: dstW, h: dstH };
    var scale = Math.max(dstW / srcW, dstH / srcH);
    var w = srcW * scale;
    var h = srcH * scale;
    return { x: (dstW - w) / 2, y: (dstH - h) / 2, w: w, h: h };
  }

  /** 等比 contain 贴合（演示模式用：整张场景图完整可见，上下留黑边，不裁构图）。 */
  function containRect(srcW, srcH, dstW, dstH) {
    if (!srcW || !srcH) return { x: 0, y: 0, w: dstW, h: dstH };
    var scale = Math.min(dstW / srcW, dstH / srcH);
    var w = srcW * scale;
    var h = srcH * scale;
    return { x: (dstW - w) / 2, y: (dstH - h) / 2, w: w, h: h };
  }

  function normalizeX(x, w) {
    return MIRROR_PREVIEW ? (1 - x - w) : x;
  }

  function sourceSize() {
    var video = $('video');
    if (state.mode === 'camera' && video && video.videoWidth > 0) {
      return { w: video.videoWidth, h: video.videoHeight };
    }
    if (state.mode === 'demo' && state.demoImage && state.demoImage.naturalWidth > 0) {
      return { w: state.demoImage.naturalWidth, h: state.demoImage.naturalHeight };
    }
    return null;
  }

  function drawOverlay() {
    var canvas = $('overlay-canvas');
    if (!canvas) return;
    var ctx = canvas.getContext('2d');
    if (!ctx) return;
    var size = ensureCanvasSize(canvas);
    if (!size.w || !size.h) return;

    ctx.setTransform(size.dpr, 0, 0, size.dpr, 0, 0);
    ctx.clearRect(0, 0, size.w, size.h);

    var src = sourceSize();
    var rect = src
      ? (state.mode === 'camera'
          ? coverRect(src.w, src.h, size.w, size.h)      // 相机：铺满屏幕，与 video 的 object-fit:cover 对齐
          : containRect(src.w, src.h, size.w, size.h))   // 演示：完整显示场景图，留黑边不裁构图
      : { x: 0, y: 0, w: size.w, h: size.h };

    // 演示模式：canvas 自己充当画面（相机模式下 video 在底下，canvas 保持透明）
    if (state.mode !== 'camera' && state.demoImage && src) {
      ctx.fillStyle = '#000';
      ctx.fillRect(0, 0, size.w, size.h);
      try {
        ctx.drawImage(state.demoImage, rect.x, rect.y, rect.w, rect.h);
      } catch (err) { /* ignore */ }
    }

    // 三分线：低透明度虚线（只做构图辅助，不是“分数”）
    ctx.save();
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.20)';
    ctx.lineWidth = 1;
    ctx.setLineDash([5, 7]);
    for (var i = 1; i <= 2; i++) {
      var x = (size.w * i) / 3;
      var y = (size.h * i) / 3;
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, size.h);
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(size.w, y);
      ctx.stroke();
    }
    ctx.restore();

    var overlay = state.overlay || {};
    // 地平线（服务端有估计才画）
    if (isNum(overlay.horizon_y) && src) {
      var hy = rect.y + overlay.horizon_y * rect.h;
      ctx.save();
      ctx.strokeStyle = 'rgba(255, 255, 255, 0.12)';
      ctx.lineWidth = 1;
      ctx.setLineDash([2, 6]);
      ctx.beginPath();
      ctx.moveTo(0, hy);
      ctx.lineTo(size.w, hy);
      ctx.stroke();
      ctx.restore();
    }

    // 主体框：橙色 1.5px
    var box = overlay.subject_box;
    if (Array.isArray(box) && box.length === 4) {
      var normX = normalizeX(box[0], box[2]);
      var bx = rect.x + normX * rect.w;
      var by = rect.y + box[1] * rect.h;
      var bw = box[2] * rect.w;
      var bh = box[3] * rect.h;
      if (bw > 1 && bh > 1) {
        ctx.save();
        ctx.strokeStyle = ORANGE;
        ctx.lineWidth = 1.5;
        ctx.strokeRect(bx, by, bw, bh);
        // 四角加粗，便于在复杂背景里辨认
        var tick = Math.min(14, bw / 4, bh / 4);
        ctx.lineWidth = 3;
        ctx.beginPath();
        ctx.moveTo(bx, by + tick); ctx.lineTo(bx, by); ctx.lineTo(bx + tick, by);
        ctx.moveTo(bx + bw - tick, by); ctx.lineTo(bx + bw, by); ctx.lineTo(bx + bw, by + tick);
        ctx.moveTo(bx + bw, by + bh - tick); ctx.lineTo(bx + bw, by + bh); ctx.lineTo(bx + bw - tick, by + bh);
        ctx.moveTo(bx + tick, by + bh); ctx.lineTo(bx, by + bh); ctx.lineTo(bx, by + bh - tick);
        ctx.stroke();
        ctx.font = '12px ' + '-apple-system, "Microsoft YaHei", sans-serif';
        ctx.fillStyle = ORANGE;
        ctx.fillText('主体', bx, Math.max(12, by - 6));
        ctx.restore();
      }
    }
  }

  /* ============================ 建议 ============================ */

  function onSuggestClick() {
    var btn = $('btn-suggest');
    if (!btn || btn.disabled) return;
    if (!state.apiOk) {
      toast('服务未启动，暂时给不了建议。先运行 scripts/start.ps1', 'error');
      return;
    }
    var input = $('intent-input');
    var intent = input ? input.value.trim() : '';
    if (state.mode !== 'camera' && !state.demoImage) {
      toast('先选一个演示场景（取景区下方缩略图），再按给建议', 'warn');
      return;
    }
    var frame = captureFrame();
    if (!frame && state.mode === 'camera') {
      toast('相机画面还没就绪，稍等一下再按一次', 'warn');
      return;
    }
    withBusy(btn, function () {
      return runSuggest(intent, frame, state.scenario);
    });
  }

  function runSuggest(intent, frame, scenario) {
    var body = {
      session_id: state.sessionId,
      intent: intent,
      frame_base64: frame
    };
    return apiFetch('/api/suggest', { method: 'POST', body: body, timeout: SUGGEST_TIMEOUT })
      .then(function (data) {
        state.suggestFailures = 0;
        onSuggestSuccess(data, intent, frame);
      })
      .catch(function (firstErr) {
        state.lastError = firstErr;
        state.suggestFailures += 1;

        // 兜底 1：抓不到帧 → 用上一次建议里的 frame_features
        if (!frame && state.lastSuggestion && state.lastSuggestion.frame_features) {
          return apiFetch('/api/suggest', {
            method: 'POST',
            body: {
              session_id: state.sessionId,
              intent: intent,
              frame_features: state.lastSuggestion.frame_features
            },
            timeout: SUGGEST_TIMEOUT
          }).then(function (data) {
            state.suggestFailures = 0;
            onSuggestSuccess(data, intent, captureFrame() || state.beforeDataUrl);
            toast('抓帧失败，已用上一帧的特征先给建议', 'warn');
          }, function (err) {
            state.suggestFailures += 1;
            throw err;
          });
        }
        throw firstErr;
      })
      .catch(function (err) {
        state.lastError = err;
        // 兜底 2：演示模式连续 3 次失败 → 回退传 expected_features（契约 §5.3）
        if (state.mode !== 'camera' && state.suggestFailures >= 3 &&
            scenario && scenario.expected_features) {
          return apiFetch('/api/suggest', {
            method: 'POST',
            body: {
              session_id: state.sessionId,
              intent: intent,
              frame_features: scenario.expected_features
            },
            timeout: SUGGEST_TIMEOUT
          }).then(function (data) {
            state.suggestFailures = 0;
            onSuggestSuccess(data, intent, frame || state.demoImage ? captureFrame() : null);
            toast('图像特征提取连续失败，已回退到场景预置特征', 'warn');
          });
        }
        throw err;
      })
      .catch(function (err) {
        var msg = (err && err.message) ? err.message : '未知错误';
        toast('建议没拿到（' + msg + '），已保留上一次建议', 'error');
      });
  }

  function onSuggestSuccess(data, intentText, frame) {
    var resp = data || {};
    state.lastSuggestion = resp;
    state.overlay = resp.overlay || null;
    state.lastScore = (resp.current_score && isNum(resp.current_score.total))
      ? resp.current_score.total : null;
    if (resp.intent_detected) state.intentId = resp.intent_detected;
    state.lastIntentText = intentText;
    if (frame) state.beforeDataUrl = frame;
    else if (state.demoImage) {
      state.beforeDataUrl = drawSourceToDataUrl(
        state.demoImage, state.demoImage.naturalWidth, state.demoImage.naturalHeight
      );
    }
    renderSuggestions(resp);
    renderDebug();
    drawOverlay();
  }

  function emptyBlock(text, title) {
    var box = document.createElement('div');
    box.className = 'advice-empty';
    if (title) {
      var t = document.createElement('p');
      t.className = 'advice-empty-title';
      t.textContent = title;
      box.appendChild(t);
    }
    var p = document.createElement('p');
    p.textContent = text;
    box.appendChild(p);
    return box;
  }

  function renderSuggestions(resp) {
    var list = $('suggestion-list');
    if (!list) return;
    list.textContent = '';

    var badge = $('intent-badge');
    var label = resp.intent_label || INTENT_LABELS[resp.intent_detected] || '';
    if (badge) {
      if (label) {
        badge.textContent = '我理解你要拍：' + label;
        badge.hidden = false;
      } else {
        badge.textContent = '';
        badge.hidden = true;
      }
    }

    var suggestions = Array.isArray(resp.suggestions) ? resp.suggestions : [];
    if (!suggestions.length) {
      // 契约：数据缺失时 suggestions 可能为空，前端必须容错
      list.appendChild(emptyBlock(
        '这次没有匹配到可执行的动作。可以换个说法（比如“想要电影感人像特写”），或者先把主体放中间再试一次。',
        '暂时没有建议'
      ));
      return;
    }

    var frag = document.createDocumentFragment();
    for (var i = 0; i < suggestions.length; i++) {
      frag.appendChild(buildSuggestionCard(suggestions[i] || {}, i));
    }
    list.appendChild(frag);
  }

  function buildSuggestionCard(item, index) {
    var rank = isNum(item.rank) ? item.rank : index + 1;
    var card = document.createElement('article');
    card.className = 'suggestion' + (rank === 1 ? ' suggestion--first' : '');
    card.setAttribute('data-rank', String(rank));
    card.setAttribute('data-action-id', item.action_id || '');

    var rankEl = document.createElement('span');
    rankEl.className = 'suggestion-rank';
    rankEl.textContent = String(rank);

    var body = document.createElement('div');
    body.className = 'suggestion-body';

    var action = document.createElement('p');
    action.className = 'suggestion-action';
    action.textContent = item.action || '（动作缺省）';
    if (item.delta) {
      var delta = document.createElement('span');
      delta.className = 'suggestion-delta';
      delta.textContent = item.delta;
      action.appendChild(delta);
    }

    var reason = document.createElement('p');
    reason.className = 'suggestion-reason';
    reason.textContent = item.reason || '';

    var acts = document.createElement('div');
    acts.className = 'suggestion-acts';
    acts.appendChild(makeSButton('useful', '有用'));
    acts.appendChild(makeSButton('useless', '没用'));
    acts.appendChild(makeSButton('adopt', '已按此调整'));

    body.appendChild(action);
    body.appendChild(reason);
    body.appendChild(acts);
    card.appendChild(rankEl);
    card.appendChild(body);
    return card;
  }

  function makeSButton(act, label) {
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 's-btn' + (act === 'adopt' ? ' s-btn--adopt' : '');
    btn.setAttribute('data-act', act);
    btn.textContent = label;
    return btn;
  }

  function onSuggestionListClick(ev) {
    var target = ev.target;
    if (!target || typeof target.closest !== 'function') return;
    var btn = target.closest('[data-act]');
    if (!btn) return;
    var card = btn.closest('.suggestion');
    if (!card) return;
    var act = btn.getAttribute('data-act');
    if (act === 'adopt') {
      state.followedAdvice = true;
      addUnique(state.adoptedActionIds, card.getAttribute('data-action-id'));
      card.classList.add('is-adopted');
      btn.classList.add('is-adopted');
      btn.textContent = '已采纳';
      btn.disabled = true;
      toast('已记下：你会按这条调整。调完点「记录镜头」。', 'ok');
      return;
    }
    if (act === 'useful' || act === 'useless') {
      var useful = act === 'useful';
      if (useful) {
        state.followedAdvice = true;
        addUnique(state.adoptedActionIds, card.getAttribute('data-action-id'));
      }
      disableFeedbackButtons(card, useful);
      sendFeedback(card, useful);
    }
  }

  function disableFeedbackButtons(card, useful) {
    var btns = card.querySelectorAll('.s-btn');
    for (var i = 0; i < btns.length; i++) {
      var act = btns[i].getAttribute('data-act');
      if (act !== 'useful' && act !== 'useless') continue;
      btns[i].disabled = true;
      if (act === 'useful' && useful) btns[i].classList.add('is-useful');
      if (act === 'useless' && !useful) btns[i].classList.add('is-useless');
    }
  }

  function sendFeedback(card, useful) {
    var actionId = card.getAttribute('data-action-id') || '';
    var rank = parseInt(card.getAttribute('data-rank'), 10);
    if (!state.apiOk) {
      toast('服务未启动：已在本机标记，反馈没有上传', 'warn');
      return;
    }
    apiFetch('/api/feedback', {
      method: 'POST',
      body: {
        session_id: state.sessionId,
        action_id: actionId,
        rank: isNum(rank) ? rank : 0,
        useful: !!useful,
        intent_id: state.intentId
      },
      timeout: 5000
    }).then(function () {
      toast(useful ? '已记录：这条有用' : '已记录：这条没用', 'ok');
    }).catch(function (err) {
      toast('反馈没发出去：' + err.message, 'error');
    });
  }

  /* ============================ 调试信息 ============================ */

  function onDebugToggle() {
    var toggle = $('debug-toggle');
    var info = $('debug-info');
    if (!toggle || !info) return;
    info.hidden = !toggle.checked;
    renderDebug();
  }

  function renderDebug() {
    var toggle = $('debug-toggle');
    var info = $('debug-info');
    if (!toggle || !info) return;
    if (!toggle.checked) {
      info.hidden = true;
      return;
    }
    info.hidden = false;
    var resp = state.lastSuggestion;
    if (!resp) {
      info.textContent = '暂无数据：先点一次「给建议」。';
      return;
    }
    var score = resp.current_score || {};
    var comps = score.components || {};
    var features = resp.frame_features || {};
    var timing = resp.timing_ms || {};
    var gap = resp.primary_gap;
    var lines = [];
    lines.push('current_score: total=' + fmtNum(score.total, 3) +
      ' | subject_ratio=' + fmtNum(comps.subject_ratio, 3) +
      ' | bg_complexity=' + fmtNum(comps.bg_complexity, 3) +
      ' | light_angle=' + fmtNum(comps.light_angle, 3) +
      ' | gaze_space=' + fmtNum(comps.gaze_space, 3));
    lines.push('frame_features: subject_ratio=' + fmtNum(features.subject_ratio, 3) +
      ' | bg_complexity=' + fmtNum(features.bg_complexity, 3) +
      ' | light_direction=' + fmtText(features.light_direction) +
      ' | gaze_space=' + fmtNum(features.gaze_space, 3));
    lines.push('timing_ms: extract=' + fmtNum(timing.extract, 1) + 'ms' +
      ' | engine=' + fmtNum(timing.engine, 1) + 'ms' +
      ' | total=' + fmtNum(timing.total, 1) + 'ms');
    if (gap && gap.field) {
      lines.push('primary_gap: ' + gap.field +
        ' ' + (gap.direction === 'low' ? '偏低' : '偏高') +
        ' (' + fmtNum(gap.weighted_gap, 3) + ')');
    } else {
      lines.push('primary_gap: 无（各维度差距都很小）');
    }
    lines.push('overlay: ' + (state.overlay ? JSON.stringify(state.overlay) : '无（服务端未返回主体框）'));
    info.textContent = lines.join('\n');
  }

  /* ============================ 记录镜头 ============================ */

  function onRecordClick() {
    var btn = $('btn-record');
    if (!btn || btn.disabled) return;
    if (!state.apiOk) {
      toast('服务未启动，记录不了镜头', 'error');
      return;
    }
    if (!isNum(state.lastScore)) {
      toast('先点一次「给建议」拿到评分，再记录这个镜头', 'warn');
      return;
    }
    var after = captureFrame();
    if (!after) {
      toast('拿不到当前画面：相机未就绪或还没选演示场景', 'error');
      return;
    }
    var before = state.beforeDataUrl || after;
    var followed = !!state.followedAdvice;
    var duration = Math.min(120, Math.max(1,
      Math.round((Date.now() - state.shotStartTs) / 100) / 10));
    var input = $('intent-input');
    var body = {
      session_id: state.sessionId,
      intent: state.lastIntentText || (input ? input.value.trim() : ''),
      intent_id: state.intentId,
      quality_score: state.lastScore,
      followed_advice: followed,
      advice_action_ids: state.adoptedActionIds.slice(),
      duration_sec: duration,
      before_image: before,
      after_image: after,
      filename: null
    };
    if (state.lastSuggestion && state.lastSuggestion.frame_features) {
      body.frame_features = state.lastSuggestion.frame_features;
    }

    withBusy(btn, function () {
      return apiFetch('/api/shots', { method: 'POST', body: body, timeout: 15000 })
        .then(function (data) {
          var resp = data || {};
          state.shotStartTs = Date.now();
          state.followedAdvice = false;
          state.adoptedActionIds = [];
          renderShotResult(resp, before, after, duration, followed);
          toast('已记录镜头 ' + fmtText(resp.clip_id), 'ok');
        })
        .catch(function (err) {
          toast('记录失败：' + err.message, 'error');
        });
    });
  }

  function makeThumb(src, caption) {
    var fig = document.createElement('figure');
    fig.className = 'shot-thumb';
    var img = document.createElement('img');
    img.src = src;
    img.alt = caption;
    var cap = document.createElement('figcaption');
    cap.textContent = caption;
    fig.appendChild(img);
    fig.appendChild(cap);
    return fig;
  }

  function renderShotResult(resp, before, after, duration, followed) {
    var box = $('shot-result');
    if (!box) return;
    box.textContent = '';
    box.hidden = false;

    var head = document.createElement('div');
    head.className = 'shot-result-head';
    var strong = document.createElement('b');
    strong.textContent = '已记录 ' + fmtText(resp.clip_id);
    head.appendChild(strong);
    head.appendChild(document.createTextNode('镜头 ' + fmtText(resp.row && resp.row.filename)));

    var tagTime = document.createElement('span');
    tagTime.className = 'shot-result-tag';
    tagTime.textContent = '时长 ' + fmtSec(duration);
    head.appendChild(tagTime);

    var tagFollow = document.createElement('span');
    tagFollow.className = 'shot-result-tag' + (followed ? ' shot-result-tag--ok' : '');
    tagFollow.textContent = followed ? '按建议调整过' : '未按建议调整';
    head.appendChild(tagFollow);

    var thumbs = document.createElement('div');
    thumbs.className = 'shot-thumbs';
    thumbs.appendChild(makeThumb(before, '调整前'));
    thumbs.appendChild(makeThumb(after, '调整后'));

    box.appendChild(head);
    box.appendChild(thumbs);
  }

  /* ============================ 前后对比 ============================ */

  function onCompareClick() {
    if (!state.beforeDataUrl) {
      toast('先点一次「给建议」：对比需要一张“调整前”的画面', 'warn');
      return;
    }
    var after = captureFrame();
    if (!after) {
      toast('拿不到当前画面：相机未就绪或还没选演示场景', 'error');
      return;
    }
    openCompareModal(state.beforeDataUrl, after);
  }

  function ensureCompareModal() {
    if (state.compareModal) return state.compareModal;
    var modal = document.createElement('div');
    modal.className = 'modal';
    modal.id = 'compare-modal';
    modal.hidden = true;

    var card = document.createElement('div');
    card.className = 'modal-card';
    var head = document.createElement('div');
    head.className = 'modal-head';
    var title = document.createElement('h3');
    title.textContent = '调整前 / 调整后';
    var closeTop = document.createElement('button');
    closeTop.type = 'button';
    closeTop.className = 'btn-ghost';
    closeTop.setAttribute('data-close', '1');
    closeTop.textContent = '关闭';
    head.appendChild(title);
    head.appendChild(closeTop);

    var wrap = document.createElement('div');
    wrap.className = 'compare-canvas-wrap';
    var canvas = document.createElement('canvas');
    canvas.id = 'compare-canvas';
    wrap.appendChild(canvas);

    var foot = document.createElement('div');
    foot.className = 'modal-foot';
    var dl = document.createElement('a');
    dl.id = 'compare-download';
    dl.className = 'btn-ghost';
    dl.setAttribute('download', '调整前后对比.png');
    dl.textContent = '下载对比图 PNG';
    var closeBottom = document.createElement('button');
    closeBottom.type = 'button';
    closeBottom.className = 'btn-ghost';
    closeBottom.setAttribute('data-close', '1');
    closeBottom.textContent = '关闭';
    foot.appendChild(dl);
    foot.appendChild(closeBottom);

    card.appendChild(head);
    card.appendChild(wrap);
    card.appendChild(foot);
    modal.appendChild(card);

    modal.addEventListener('click', function (ev) {
      var t = ev.target;
      if (t === modal) closeCompareModal();
      if (t && typeof t.closest === 'function' && t.closest('[data-close]')) closeCompareModal();
    });
    document.addEventListener('keydown', function (ev) {
      if (ev.key === 'Escape' && !modal.hidden) closeCompareModal();
    });

    document.body.appendChild(modal);
    state.compareModal = modal;
    return modal;
  }

  function openCompareModal(beforeUrl, afterUrl) {
    var modal = ensureCompareModal();
    modal.hidden = false;
    var canvas = $('compare-canvas');
    if (!canvas) return;
    Promise.all([loadImage(beforeUrl), loadImage(afterUrl)]).then(function (images) {
      drawCompareCanvas(canvas, images[0], images[1]);
      var dl = $('compare-download');
      if (dl) {
        try { dl.href = canvas.toDataURL('image/png'); } catch (err) { dl.removeAttribute('href'); }
      }
    }).catch(function (err) {
      toast('对比图绘制失败：' + err.message, 'error');
    });
  }

  function closeCompareModal() {
    if (state.compareModal) state.compareModal.hidden = true;
  }

  function drawCompareCanvas(canvas, beforeImg, afterImg) {
    var scale = Math.min(2, Math.max(1, window.devicePixelRatio || 1));
    var cellW = 560;
    var cellH = 360;
    var gap = 18;
    var labelH = 34;
    var cssW = cellW * 2 + gap;
    var cssH = cellH + labelH + 18;
    canvas.width = Math.round(cssW * scale);
    canvas.height = Math.round(cssH * scale);
    canvas.style.width = cssW + 'px';
    canvas.style.height = cssH + 'px';

    var ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(scale, 0, 0, scale, 0, 0);
    ctx.fillStyle = '#0a0b0e';
    ctx.fillRect(0, 0, cssW, cssH);

    function drawCell(img, x, label) {
      var y = labelH;
      ctx.fillStyle = '#000';
      ctx.fillRect(x, y, cellW, cellH);
      var rect = coverRect(img.naturalWidth, img.naturalHeight, cellW, cellH);
      // 对比图用 contain，避免裁掉构图信息
      var containScale = Math.min(cellW / img.naturalWidth, cellH / img.naturalHeight);
      var w = img.naturalWidth * containScale;
      var h = img.naturalHeight * containScale;
      var dx = x + (cellW - w) / 2;
      var dy = y + (cellH - h) / 2;
      try { ctx.drawImage(img, dx, dy, w, h); } catch (err) { /* ignore */ }
      ctx.strokeStyle = 'rgba(255,255,255,0.10)';
      ctx.lineWidth = 1;
      ctx.strokeRect(x + 0.5, y + 0.5, cellW - 1, cellH - 1);
      // 标签
      ctx.fillStyle = label === '调整前' ? '#9fb4c9' : '#E8752C';
      ctx.fillRect(x, 0, 4, labelH - 10);
      ctx.fillStyle = '#eef1f5';
      ctx.font = '600 15px ' + '-apple-system, "Microsoft YaHei", sans-serif';
      ctx.fillText(label, x + 12, labelH - 13);
      // 参考矩形变量（coverRect 只用于占位，保持与叠加层一致的映射说明）
      if (!rect) return;
    }

    drawCell(beforeImg, 0, '调整前');
    drawCell(afterImg, cellW + gap, '调整后');
  }

  /* ============================ 结束拍摄 / 粗剪 ============================ */

  function isPanelHidden() {
    var panel = $('panel-cut');
    return !panel || panel.hidden;
  }

  function setCutPanel(open) {
    var panel = $('panel-cut');
    var btn = $('btn-finish');
    if (!panel) return;
    panel.hidden = !open;
    if (btn) btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (open) {
      try { panel.scrollIntoView({ behavior: 'smooth', block: 'start' }); } catch (err) { panel.scrollIntoView(); }
    }
  }

  function ensureDownloadLinks() {
    var edl = $('dl-edl');
    var bat = $('dl-bat');
    if (edl) {
      edl.setAttribute('href', apiUrl('/api/edit/export/rough_cut.json'));
      edl.setAttribute('download', 'rough_cut.json');
    }
    if (bat) {
      bat.setAttribute('href', apiUrl('/api/edit/export/rough_cut.bat'));
      bat.setAttribute('download', 'rough_cut.bat');
    }
  }

  function onFinishClick() {
    var btn = $('btn-finish');
    if (!btn || btn.disabled) return;
    if (!state.apiOk) {
      toast('服务未启动，生成不了粗剪', 'error');
      return;
    }
    setCutPanel(true);
    withBusy(btn, loadEdit);
  }

  function setPanelBusy(busy) {
    var select = $('cut-source');
    if (select) select.disabled = !!busy;
  }

  function loadEdit() {
    var note = $('cut-note');
    var select = $('cut-source');
    var source = (select && select.value) || 'materials';
    if (note) {
      note.className = 'panel-note';
      note.textContent = '正在整理素材、生成粗剪…';
    }
    setPanelBusy(true);
    return apiFetch('/api/edit', { method: 'POST', body: { source: source }, timeout: 20000 })
      .then(function (data) {
        renderEdit(data || {}, source);
      })
      .catch(function (err) {
        if (note) {
          note.className = 'panel-note panel-note--error';
          note.textContent = '生成粗剪失败：' + err.message + '（可以点「结束拍摄」重试）';
        }
        clearEditNumbers();
        toast('生成粗剪失败：' + err.message, 'error');
      })
      .then(function () {
        setPanelBusy(false);
      });
  }

  function clearEditNumbers() {
    var ids = ['org-total', 'org-usable', 'org-excluded', 'org-duplicate'];
    for (var i = 0; i < ids.length; i++) {
      var el = $(ids[i]);
      if (el) el.textContent = '–';
    }
    var total = $('cut-total');
    if (total) total.textContent = '';
    var tbody = $('cut-timeline-body');
    if (tbody) tbody.textContent = '';
    hideBlock('cut-excluded-block');
    hideBlock('cut-timeline-block');
    hideBlock('cut-warnings-block');
    var ff = $('cut-ffmpeg');
    if (ff) ff.textContent = '';
  }

  function hideBlock(id) {
    var el = $(id);
    if (el) el.hidden = true;
  }

  function countOf(value) {
    if (typeof value === 'number' && isFinite(value)) return value;
    if (Array.isArray(value)) return value.length;
    return 0;
  }

  function setListItems(listEl, items) {
    if (!listEl) return;
    listEl.textContent = '';
    for (var i = 0; i < items.length; i++) {
      var li = document.createElement('li');
      li.textContent = items[i];
      listEl.appendChild(li);
    }
  }

  function renderEdit(data, requestedSource) {
    var org = data.organization || {};
    var roughCut = data.rough_cut || {};
    var note = $('cut-note');

    var total = org.total;
    if (!isNum(total)) total = countOf(org.total);
    var usable = isNum(org.usable) ? org.usable : countOf(org.usable);
    var excludedCount = isNum(org.excluded_count) ? org.excluded_count : countOf(org.excluded);
    var duplicateCount = isNum(org.duplicate_count) ? org.duplicate_count : countOf(org.duplicates);

    var elTotal = $('org-total');
    if (elTotal) elTotal.textContent = String(total);
    var elUsable = $('org-usable');
    if (elUsable) elUsable.textContent = String(usable);
    var elExcluded = $('org-excluded');
    if (elExcluded) elExcluded.textContent = String(excludedCount);
    var elDuplicate = $('org-duplicate');
    if (elDuplicate) elDuplicate.textContent = String(duplicateCount);

    if (note) {
      note.className = 'panel-note';
      note.textContent = '素材源：' + fmtText(data.source || requestedSource) +
        ' · 已按“质量筛选 → 意图加分 → 叙事排序”生成粗剪';
    }

    // 剔除与重复理由
    var excluded = Array.isArray(org.excluded) ? org.excluded : [];
    var duplicates = Array.isArray(org.duplicates) ? org.duplicates : [];
    if (excluded.length || duplicates.length) {
      var excludedTexts = [];
      for (var i = 0; i < excluded.length; i++) {
        var e = excluded[i] || {};
        excludedTexts.push(
          fmtText(e.clip_id) + ' · ' + fmtText(e.reason) +
          (e.rule_id ? '（规则 ' + e.rule_id + '）' : '')
        );
      }
      var duplicateTexts = [];
      for (var j = 0; j < duplicates.length; j++) {
        var d = duplicates[j] || {};
        duplicateTexts.push(
          fmtText(d.clip_id) + ' 与 ' + fmtText(d.duplicate_of) + ' 重复' +
          (d.action ? '（' + d.action + '）' : '')
        );
      }
      setListItems($('cut-excluded'), excludedTexts.length ? excludedTexts : ['无']);
      setListItems($('cut-duplicates'), duplicateTexts.length ? duplicateTexts : ['无']);
      var block = $('cut-excluded-block');
      if (block) block.hidden = false;
    } else {
      hideBlock('cut-excluded-block');
    }

    // 时间线表格
    var timeline = Array.isArray(roughCut.timeline) ? roughCut.timeline : [];
    var tbody = $('cut-timeline-body');
    if (tbody && timeline.length) {
      tbody.textContent = '';
      for (var k = 0; k < timeline.length; k++) {
        var clip = timeline[k] || {};
        var tr = document.createElement('tr');
        var tdOrder = document.createElement('td');
        tdOrder.className = 'num';
        tdOrder.textContent = fmtText(isNum(clip.order) ? clip.order : k + 1);

        var tdName = document.createElement('td');
        tdName.className = 'name';
        tdName.textContent = clip.filename || clip.clip_id || '—';

        var tdDur = document.createElement('td');
        tdDur.textContent = fmtSec(isNum(clip.duration_sec)
          ? clip.duration_sec
          : (isNum(clip.out) && isNum(clip.in) ? clip.out - clip.in : NaN));

        var tdTrans = document.createElement('td');
        tdTrans.textContent = fmtText(clip.transition_in);

        var tdReason = document.createElement('td');
        tdReason.className = 'reason';
        tdReason.textContent = fmtText(clip.reason);

        tr.appendChild(tdOrder);
        tr.appendChild(tdName);
        tr.appendChild(tdDur);
        tr.appendChild(tdTrans);
        tr.appendChild(tdReason);
        tbody.appendChild(tr);
      }
      var timelineBlock = $('cut-timeline-block');
      if (timelineBlock) timelineBlock.hidden = false;
    } else {
      hideBlock('cut-timeline-block');
    }

    // 总时长
    var totalDuration = roughCut.total_duration_sec;
    if (!isNum(totalDuration)) {
      totalDuration = 0;
      for (var m = 0; m < timeline.length; m++) {
        if (isNum(timeline[m].duration_sec)) totalDuration += timeline[m].duration_sec;
      }
    }
    var clipCount = isNum(roughCut.clip_count) ? roughCut.clip_count : timeline.length;
    var totalEl = $('cut-total');
    if (totalEl) {
      totalEl.textContent = '';
      totalEl.appendChild(document.createTextNode('粗剪总时长 '));
      var b = document.createElement('b');
      b.textContent = fmtSec(totalDuration);
      totalEl.appendChild(b);
      totalEl.appendChild(document.createTextNode(' · 共 ' + clipCount + ' 段'));
    }

    // warnings
    var warnings = Array.isArray(data.warnings) ? data.warnings : [];
    if (warnings.length) {
      var warnTexts = [];
      for (var n = 0; n < warnings.length; n++) warnTexts.push(String(warnings[n]));
      setListItems($('cut-warnings'), warnTexts);
      var warnBlock = $('cut-warnings-block');
      if (warnBlock) warnBlock.hidden = false;
    } else {
      hideBlock('cut-warnings-block');
    }

    // ffmpeg 说明（本机没有 ffmpeg 也要给完整命令/脚本，不报错）
    var ff = data.ffmpeg || {};
    var ffNote = $('cut-ffmpeg');
    if (ffNote) {
      if (ff.available === true) {
        ffNote.textContent = '本机检测到 ffmpeg：下载 .bat 后可直接执行渲染粗剪。';
      } else {
        ffNote.textContent = '本机未检测到 ffmpeg：已生成可执行的 FFmpeg 命令脚本（.bat）与 EDL JSON，' +
          '可拷到装有 ffmpeg 的机器上渲染。';
      }
    }

    ensureDownloadLinks();
    renderDebug();
  }

  /* ============================ 事件绑定 ============================ */

  function bindEvents() {
    var suggestBtn = $('btn-suggest');
    if (suggestBtn) suggestBtn.addEventListener('click', onSuggestClick);

    var input = $('intent-input');
    if (input) {
      input.addEventListener('keydown', function (ev) {
        if (ev.key === 'Enter' && !ev.isComposing) {
          ev.preventDefault();
          onSuggestClick();
        }
      });
    }

    var recordBtn = $('btn-record');
    if (recordBtn) recordBtn.addEventListener('click', onRecordClick);

    var compareBtn = $('btn-compare');
    if (compareBtn) compareBtn.addEventListener('click', onCompareClick);

    var finishBtn = $('btn-finish');
    if (finishBtn) finishBtn.addEventListener('click', onFinishClick);

    var closeCut = $('btn-close-cut');
    if (closeCut) {
      closeCut.addEventListener('click', function () { setCutPanel(false); });
    }

    var cutSource = $('cut-source');
    if (cutSource) {
      cutSource.addEventListener('change', function () {
        if (!isPanelHidden()) loadEdit();
      });
    }

    var debugToggle = $('debug-toggle');
    if (debugToggle) debugToggle.addEventListener('change', onDebugToggle);

    var statusEl = $('status');
    if (statusEl) statusEl.addEventListener('click', retryHealthNow);

    var list = $('suggestion-list');
    if (list) list.addEventListener('click', onSuggestionListClick);

    var demos = $('demo-scenarios');
    if (demos) {
      demos.addEventListener('click', function (ev) {
        var t = ev.target;
        if (!t || typeof t.closest !== 'function') return;
        var btn = t.closest('.demo-thumb');
        if (!btn) return;
        selectScenario(btn.getAttribute('data-id'), false);
      });
    }
  }

  /* ============================ 入口 ============================ */

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
