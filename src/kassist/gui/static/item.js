// kassist item editor: approve button, unsaved guard, photo sorting (drag, mouse + touch), photo dialog
// (view, crop, rotate, cover, leave out), category picker (hierarchy + search). No dependencies.
(() => {
  'use strict';
  const form = document.getElementById('listing-form');
  if (!form) return;
  const unsaved = document.getElementById('unsaved');
  let dirty = false;
  const markDirty = () => { dirty = true; if (unsaved) unsaved.hidden = false; };
  form.addEventListener('input', markDirty);
  form.addEventListener('change', markDirty);
  form.addEventListener('submit', () => { dirty = false; });

  // Approve / withdraw approval: set the field and save everything in one go.
  document.querySelectorAll('[data-approve]').forEach((btn) => btn.addEventListener('click', () => {
    document.getElementById('approved').value = btn.dataset.approve;
    dirty = false;
    form.requestSubmit();
  }));

  // Buttons that leave the page: warn about unsaved edits, confirm risky ones.
  document.querySelectorAll('[data-guard]').forEach((btn) => btn.addEventListener('click', (ev) => {
    if (dirty && !confirm(form.dataset.unsavedText + ' – ' + form.dataset.confirmText)) { ev.preventDefault(); return; }
    if (btn.hasAttribute('data-confirm') && !confirm(form.dataset.confirmText)) ev.preventDefault();
    else dirty = false;
  }));
  window.addEventListener('beforeunload', (ev) => { if (dirty) { ev.preventDefault(); ev.returnValue = ''; } });

  // --- photos: drag to sort, first = cover ------------------------------------------------------
  const grid = document.getElementById('photos');
  const refresh = () => {
    if (!grid) return;
    let coverSet = false;
    grid.querySelectorAll('.ka-photo').forEach((fig) => {
      const skipped = fig.querySelector('input[name="photos.skip"]').checked;
      fig.toggleAttribute('data-skipped', skipped);
      const cover = !skipped && !coverSet;
      if (cover) coverSet = true;
      fig.toggleAttribute('data-cover', cover);
    });
  };
  let openPhoto = () => {};
  if (grid) {
    // Smooth sorting: the photo follows the pointer as a ghost, a gap moves through the grid, the others glide (FLIP).
    let drag = null;   // {fig, x, y, dx, dy, ghost}
    const tiles = () => [...grid.querySelectorAll('.ka-photo')];
    const glide = (mutate) => {
      const before = new Map(tiles().map((el) => [el, el.getBoundingClientRect()]));
      mutate();
      tiles().forEach((el) => {
        const a = before.get(el), b = el.getBoundingClientRect();
        if (!a || el === drag?.fig) return;
        const dx = a.left - b.left, dy = a.top - b.top;
        if (!dx && !dy) return;
        el.animate([{ transform: `translate(${dx}px, ${dy}px)` }, { transform: 'none' }], { duration: 160, easing: 'ease-out' });
      });
    };
    grid.addEventListener('pointerdown', (ev) => {
      const fig = ev.target.closest('.ka-photo');
      if (!fig || ev.button > 0) return;
      const r = fig.getBoundingClientRect();
      drag = { fig, x: ev.clientX, y: ev.clientY, dx: ev.clientX - r.left, dy: ev.clientY - r.top, ghost: null, w: r.width, h: r.height };
      grid.setPointerCapture(ev.pointerId);
    });
    grid.addEventListener('pointermove', (ev) => {
      if (!drag) return;
      if (!drag.ghost) {
        if (Math.hypot(ev.clientX - drag.x, ev.clientY - drag.y) < 6) return;
        const g = document.createElement('figure');
        g.className = 'ka-photo-ghost';
        g.style.width = `${drag.w}px`; g.style.height = `${drag.h}px`;
        g.append(drag.fig.querySelector('img').cloneNode());
        document.body.append(g);
        drag.ghost = g;
        drag.fig.setAttribute('data-placeholder', '');
      }
      drag.ghost.style.left = `${ev.clientX - drag.dx}px`;
      drag.ghost.style.top = `${ev.clientY - drag.dy}px`;
      const over = document.elementFromPoint(ev.clientX, ev.clientY)?.closest('.ka-photo');
      if (!over || over === drag.fig || over.parentElement !== grid) return;
      const list = tiles();
      const after = list.indexOf(over) > list.indexOf(drag.fig);
      glide(() => grid.insertBefore(drag.fig, after ? over.nextElementSibling : over));
      refresh();
    });
    const end = () => {
      if (!drag) return;
      const { fig, ghost } = drag;
      drag = null;
      if (ghost) {
        ghost.remove();
        fig.removeAttribute('data-placeholder');
        glide(refresh);
        markDirty();
      } else {
        openPhoto(fig);
      }
    };
    grid.addEventListener('pointerup', end);
    grid.addEventListener('pointercancel', end);
    refresh();
  }

  // --- photo dialog: view, crop, rotate, cover, leave out ---------------------------------------
  const dialog = document.getElementById('photo');
  const canvas = document.getElementById('crop-canvas');
  const aspectSel = document.getElementById('crop-aspect');
  const skipBtn = document.getElementById('photo-skip');
  if (dialog && canvas) {
    const ctx = canvas.getContext('2d');
    let img = null, current = null, rot = 0, sel = null, start = null, scale = 1;
    const rotatedSize = () => (rot % 180 === 0 ? [img.naturalWidth, img.naturalHeight] : [img.naturalHeight, img.naturalWidth]);
    const drawRotated = (c, w, h) => {
      const [rw, rh] = rotatedSize();
      c.save();
      c.translate(w / 2, h / 2);
      c.rotate((rot * Math.PI) / 180);
      const iw = img.naturalWidth * (w / rw), ih = img.naturalHeight * (h / rh);
      c.drawImage(img, -iw / 2, -ih / 2, iw, ih);
      c.restore();
    };
    // Fit the picture into the free space of the dialog (window minus header, tools and footer).
    const render = () => {
      const [rw, rh] = rotatedSize();
      // free space = window minus everything in the dialog that is not the picture
      const boxEl = dialog.querySelector('.ka-crop-box');
      const stage = dialog.querySelector('.ka-crop-stage');
      const chrome = boxEl.getBoundingClientRect().height - stage.getBoundingClientRect().height;
      const maxW = Math.max(200, stage.getBoundingClientRect().width);
      const maxH = Math.max(160, window.innerHeight * 0.94 - chrome - 8);
      scale = Math.min(maxW / rw, maxH / rh, 1);
      canvas.width = Math.round(rw * scale);
      canvas.height = Math.round(rh * scale);
      drawRotated(ctx, canvas.width, canvas.height);
      if (sel && sel.w > 2 && sel.h > 2) {
        ctx.fillStyle = 'rgba(0,0,0,.45)';
        ctx.fillRect(0, 0, canvas.width, sel.y);
        ctx.fillRect(0, sel.y + sel.h, canvas.width, canvas.height - sel.y - sel.h);
        ctx.fillRect(0, sel.y, sel.x, sel.h);
        ctx.fillRect(sel.x + sel.w, sel.y, canvas.width - sel.x - sel.w, sel.h);
        ctx.strokeStyle = '#fff';
        ctx.lineWidth = 2;
        ctx.strokeRect(sel.x, sel.y, sel.w, sel.h);
      }
    };
    window.addEventListener('resize', () => { if (dialog.open && img) { sel = null; render(); } });
    const pos = (ev) => {
      const r = canvas.getBoundingClientRect();
      return [(ev.clientX - r.left) * (canvas.width / r.width), (ev.clientY - r.top) * (canvas.height / r.height)];
    };
    canvas.addEventListener('pointerdown', (ev) => { start = pos(ev); sel = null; canvas.setPointerCapture(ev.pointerId); });
    canvas.addEventListener('pointermove', (ev) => {
      if (!start) return;
      let [x, y] = pos(ev);
      x = Math.max(0, Math.min(canvas.width, x));
      y = Math.max(0, Math.min(canvas.height, y));
      const w = x - start[0];
      let h = y - start[1];
      const aspect = parseFloat(aspectSel.value);
      if (aspect > 0) h = Math.sign(h || 1) * Math.abs(w) / aspect;
      sel = { x: Math.min(start[0], start[0] + w), y: Math.min(start[1], start[1] + h), w: Math.abs(w), h: Math.abs(h) };
      sel.w = Math.min(sel.w, canvas.width - sel.x);
      sel.h = Math.min(sel.h, canvas.height - sel.y);
      render();
    });
    canvas.addEventListener('pointerup', () => { start = null; });
    dialog.querySelectorAll('[data-rotate]').forEach((b) => b.addEventListener('click', () => {
      rot = (rot + parseInt(b.dataset.rotate, 10) + 360) % 360;
      sel = null;
      render();
    }));
    aspectSel.addEventListener('change', () => { sel = null; render(); });

    const syncSkip = () => {
      const skipped = current.querySelector('input[name="photos.skip"]').checked;
      skipBtn.textContent = skipped ? skipBtn.dataset.off : skipBtn.dataset.on;
    };
    openPhoto = (fig) => {
      current = fig;
      rot = 0; sel = null;
      img = new Image();
      img.onload = () => { dialog.showModal(); requestAnimationFrame(render); };
      img.src = fig.dataset.full;
      syncSkip();
    };
    document.getElementById('photo-cover').addEventListener('click', () => {
      current.querySelector('input[name="photos.skip"]').checked = false;
      grid.prepend(current);
      refresh(); markDirty(); syncSkip();
    });
    skipBtn.addEventListener('click', () => {
      const box = current.querySelector('input[name="photos.skip"]');
      box.checked = !box.checked;
      refresh(); markDirty(); syncSkip();
    });
    document.getElementById('crop-save').addEventListener('click', () => {
      const [rw, rh] = rotatedSize();
      const full = document.createElement('canvas');
      full.width = rw; full.height = rh;
      drawRotated(full.getContext('2d'), rw, rh);
      const area = sel && sel.w > 2 && sel.h > 2
        ? { x: sel.x / scale, y: sel.y / scale, w: sel.w / scale, h: sel.h / scale }
        : { x: 0, y: 0, w: rw, h: rh };
      const out = document.createElement('canvas');
      out.width = Math.round(area.w); out.height = Math.round(area.h);
      out.getContext('2d').drawImage(full, area.x, area.y, area.w, area.h, 0, 0, out.width, out.height);
      out.toBlob((blob) => {
        const body = new FormData();
        body.append('source', current.dataset.name);
        body.append('file', blob, 'crop.jpg');
        fetch(`/items/${encodeURIComponent(dialog.dataset.key)}/photos/crop`, { method: 'POST', body })
          .then((r) => r.json())
          .then((res) => {
            if (res.error) { alert(res.error); return; }
            if (res.warning) alert(res.warning);
            dirty = false;
            location.reload();
          });
      }, 'image/jpeg', 0.92);
    });
  }

  // --- category picker: columns by hierarchy, or search while typing -----------------------------
  const cat = document.getElementById('cat');
  const data = document.getElementById('cat-data');
  if (cat && data) {
    const names = JSON.parse(data.textContent);
    const tree = {};
    names.forEach((full) => {
      let node = tree;
      full.split(' > ').forEach((part, i, parts) => {
        node[part] = node[part] || { _full: parts.slice(0, i + 1).join(' > '), _sel: false, _kids: {} };
        if (i === parts.length - 1) node[part]._sel = true;
        node = node[part]._kids;
      });
    });
    const hidden = cat.querySelector('input[type=hidden]');
    const button = cat.querySelector('.ka-cat-button');
    const panel = cat.querySelector('.ka-cat-panel');
    const search = panel.querySelector('input[type=search]');
    const body = panel.querySelector('.ka-cat-body');
    let path = (hidden.value || '').split(' > ').filter(Boolean);

    const choose = (full) => {
      hidden.value = full;
      button.textContent = full || cat.dataset.choose;
      panel.hidden = true;
      markDirty();
    };
    const item = (label, onClick, extra = '') => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'ka-cat-item' + extra;
      b.textContent = label;
      b.addEventListener('click', onClick);
      return b;
    };
    const columns = () => {
      body.replaceChildren();
      body.className = 'ka-cat-body ka-cat-cols';
      let level = tree;
      for (let depth = 0; depth <= path.length; depth++) {
        const col = document.createElement('div');
        col.className = 'ka-cat-col';
        if (depth > 0) {
          const parent = path.slice(0, depth).reduce((n, p, i) => (i === 0 ? tree[p] : n._kids[p]), null);
          if (parent && parent._sel) col.append(item(cat.dataset.this, () => choose(parent._full), ' ka-cat-this'));
        }
        Object.keys(level).sort((a, b) => a.localeCompare(b, 'de')).forEach((name) => {
          const node = level[name];
          const hasKids = Object.keys(node._kids).length > 0;
          const active = path[depth] === name;
          col.append(item(name + (hasKids ? ' ›' : ''), () => {
            if (hasKids) { path = path.slice(0, depth).concat(name); columns(); } else { choose(node._full); }
          }, active ? ' ka-cat-active' : ''));
        });
        body.append(col);
        const next = path[depth] && level[path[depth]];
        if (!next || !Object.keys(next._kids).length) break;
        level = next._kids;
      }
    };
    const results = (q) => {
      const words = q.toLowerCase().split(/\s+/).filter(Boolean);
      body.replaceChildren();
      body.className = 'ka-cat-body ka-cat-list';
      const hits = names.filter((n) => words.every((w) => n.toLowerCase().includes(w))).slice(0, 60);
      if (!hits.length) { body.textContent = cat.dataset.none; return; }
      hits.forEach((full) => body.append(item(full, () => choose(full))));
    };
    search.addEventListener('input', () => (search.value.trim() ? results(search.value) : columns()));
    search.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter') { ev.preventDefault(); body.querySelector('.ka-cat-item')?.click(); }
      if (ev.key === 'Escape') panel.hidden = true;
    });
    // The panel floats above the page (fixed), so cards with hidden overflow cannot clip it.
    const place = () => {
      const r = button.getBoundingClientRect();
      const width = Math.min(Math.max(r.width, 520), window.innerWidth - 16);
      const left = Math.min(r.left, window.innerWidth - width - 8);
      const below = window.innerHeight - r.bottom - 12;
      panel.style.left = `${Math.max(8, left)}px`;
      panel.style.width = `${width}px`;
      if (below >= 260 || below >= r.top) { panel.style.top = `${r.bottom + 4}px`; panel.style.bottom = ''; panel.style.maxHeight = `${below}px`; }
      else { panel.style.bottom = `${window.innerHeight - r.top + 4}px`; panel.style.top = ''; panel.style.maxHeight = `${r.top - 12}px`; }
    };
    button.addEventListener('click', () => {
      panel.hidden = !panel.hidden;
      if (!panel.hidden) { place(); search.value = ''; columns(); search.focus({ preventScroll: true }); }
    });
    document.addEventListener('pointerdown', (ev) => { if (!cat.contains(ev.target)) panel.hidden = true; });
    document.addEventListener('scroll', () => { if (!panel.hidden) place(); }, true);
    window.addEventListener('resize', () => { if (!panel.hidden) place(); });
  }
  // --- AI proposals: improved text, titles, categories (filled into the form, saved with Save) -------
  const aiDialog = document.getElementById('ai-dialog');
  if (aiDialog) {
    const body = document.getElementById('ai-body');
    const heading = document.getElementById('ai-title');
    const field = (name) => form.querySelector(`[name="${name}"]`);
    const setValue = (name, value) => {
      const el = field(name);
      el.value = value;
      el.dispatchEvent(new Event('input', { bubbles: true }));
    };
    const option = (label, onUse) => {
      const row = document.createElement('div');
      row.className = 'ka-ai-option';
      const span = document.createElement('span');
      span.textContent = label;
      const b = document.createElement('button');
      b.type = 'button'; b.className = 'btn'; b.dataset.size = 'sm'; b.textContent = aiDialog.dataset.use;
      b.addEventListener('click', () => { onUse(); aiDialog.close(); });
      row.append(span, b);
      return row;
    };
    document.querySelectorAll('[data-ai]').forEach((btn) => btn.addEventListener('click', async () => {
      const what = btn.dataset.ai;
      heading.textContent = aiDialog.dataset['t' + what[0].toUpperCase() + what.slice(1)];
      body.replaceChildren(Object.assign(document.createElement('p'), { className: 'ka-muted', textContent: aiDialog.dataset.working }));
      aiDialog.showModal();
      let res;
      try {
        const r = await fetch(`/items/${encodeURIComponent(aiDialog.dataset.key)}/ai/${what}`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ title: field('title').value, description: field('description').value }) });
        res = await r.json();
      } catch (e) { res = { error: String(e) }; }
      body.replaceChildren();
      if (res.error) { body.append(Object.assign(document.createElement('p'), { textContent: res.error })); return; }
      if (what === 'text') {
        const area = Object.assign(document.createElement('textarea'), { className: 'textarea', rows: 14, value: res.text });
        body.append(area, option('', () => setValue('description', area.value)));
      } else if (what === 'titles') {
        res.titles.forEach((title) => body.append(option(title, () => setValue('title', title))));
      } else {
        res.categories.forEach((name) => body.append(option(name, () => {
          const cat = document.getElementById('cat');
          cat.querySelector('input[type=hidden]').value = name;
          cat.querySelector('.ka-cat-button').textContent = name;
          markDirty();
        })));
      }
    }));
  }
  // --- camera: take photos straight into the item folder -------------------------------------------
  const cam = document.getElementById('cam');
  if (cam) {
    const video = document.getElementById('cam-video');
    const shot = document.getElementById('cam-shot');
    const devices = document.getElementById('cam-device');
    const count = document.getElementById('cam-count');
    const take = document.getElementById('cam-take'), keep = document.getElementById('cam-use'), again = document.getElementById('cam-retake');
    let stream = null, saved = 0;
    const stop = () => { if (stream) stream.getTracks().forEach((tr) => tr.stop()); stream = null; };
    const preview = (on) => { shot.hidden = !on; video.hidden = on; take.hidden = on; keep.hidden = !on; again.hidden = !on; };
    const start = async (deviceId) => {
      stop();
      try {
        stream = await navigator.mediaDevices.getUserMedia({ video: deviceId ? { deviceId: { exact: deviceId }, width: { ideal: 3840 } } : { width: { ideal: 3840 } }, audio: false });
      } catch (e) { alert(cam.dataset.denied + '\n' + e.name); cam.close(); return; }
      video.srcObject = stream;
      const list = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === 'videoinput');
      const current = stream.getVideoTracks()[0]?.getSettings().deviceId;
      devices.replaceChildren(...list.map((d, i) => Object.assign(document.createElement('option'),
        { value: d.deviceId, textContent: d.label || `Kamera ${i + 1}`, selected: d.deviceId === current })));
      devices.hidden = list.length < 2;
      preview(false);
    };
    document.getElementById('cam-open').addEventListener('click', () => {
      if (dirty && !confirm(form.dataset.unsavedText + ' – ' + form.dataset.confirmText)) return;
      saved = 0; count.textContent = ''; cam.showModal(); start();
    });
    devices.addEventListener('change', () => start(devices.value));
    take.addEventListener('click', () => {
      shot.width = video.videoWidth; shot.height = video.videoHeight;
      shot.getContext('2d').drawImage(video, 0, 0);
      preview(true);
    });
    again.addEventListener('click', () => preview(false));
    keep.addEventListener('click', () => shot.toBlob(async (blob) => {
      const body = new FormData();
      body.append('file', blob, 'camera.jpg');
      const r = await fetch(`/items/${encodeURIComponent(cam.dataset.key)}/photos/capture`, { method: 'POST', body });
      const res = await r.json();
      if (res.error) { alert(res.error); return; }
      saved += 1; count.textContent = `${saved} ✓`;
      preview(false);
    }, 'image/jpeg', 0.92));
    const close = () => { stop(); cam.close(); if (saved) { dirty = false; location.reload(); } };
    document.getElementById('cam-close').addEventListener('click', close);
    cam.addEventListener('cancel', (ev) => { ev.preventDefault(); close(); });
  }
  // standard texts: preview below the description follows the ticks
  form.querySelectorAll('input[name="texts"]').forEach((box) => box.addEventListener('change', () => {
    const block = form.querySelector(`.ka-block-text[data-block="${CSS.escape(box.value)}"]`);
    if (block) block.hidden = !box.checked;
  }));
  // key data: the ticked ones are previewed where they will stand (after the item text)
  const factsBox = document.getElementById('facts');
  const factsPreview = document.getElementById('facts-preview');
  if (factsBox && factsPreview) {
    const euro = (v) => (Number.isInteger(v) ? `${v} €` : `${v.toFixed(2).replace('.', ',')} €`);
    const update = () => {
      const lines = [];
      factsBox.querySelectorAll('[data-fact-show]').forEach((box) => {
        if (!box.checked) return;
        const row = [box.previousElementSibling, box.previousElementSibling?.previousElementSibling];
        const cond = box.name === 'facts.condition.show' ? document.getElementById('f-condition') : null;
        if (cond) { const o = cond.selectedOptions[0]; if (o && o.dataset.text) lines.push(o.dataset.text); return; }
        const num = row[0]?.querySelector?.('[data-fact-line]');
        if (num) {
          const v = parseFloat((num.value || '').replace(',', '.'));
          if (!Number.isNaN(v)) lines.push(num.dataset.factLine.replace('{v}', euro(v)));
          return;
        }
        if (row[0]?.matches?.('[data-fact-extra]') && row[0].value.trim() && row[1].value.trim()) {
          lines.push(`${row[1].value.trim()}: ${row[0].value.trim()}`);
        }
      });
      factsPreview.textContent = lines.join('\n');
      factsPreview.hidden = !lines.length;
    };
    factsBox.addEventListener('input', update);
    factsBox.addEventListener('change', update);
    factsBox.querySelectorAll('[data-copy-to]').forEach((b) => b.addEventListener('click', () => {
      const target = document.getElementById(b.dataset.copyTo);
      target.value = b.dataset.value;
      target.dispatchEvent(new Event('input', { bubbles: true }));
    }));
    update();
  }
  // price steps: equal steps (count) or a fixed step, switched per item
  const fixedBox = document.getElementById('fixed-step');
  if (fixedBox) {
    const mode = () => document.querySelectorAll('[data-step-mode]').forEach((el) => {
      el.hidden = (el.dataset.stepMode === 'fixed') !== fixedBox.checked;
    });
    fixedBox.addEventListener('change', mode);
    mode();
  }
})();
